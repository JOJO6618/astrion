use std::{
    collections::VecDeque,
    io,
    path::PathBuf,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde_json::Value;
use uuid::Uuid;

use super::{Action, CrossPetConfig, CrossPetRuntime, TaskEvent, files, projection::Projection};

const EVENT_LIMIT: usize = 2048;
const COALESCE: Duration = Duration::from_millis(150);
const PROBE_INTERVAL: Duration = Duration::from_secs(2);
const HEARTBEAT: Duration = Duration::from_secs(10);
const LOG_INTERVAL: Duration = Duration::from_secs(60);

pub(super) struct Control {
    shared: Arc<Shared>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

struct Shared {
    config: CrossPetConfig,
    runtime: Arc<dyn CrossPetRuntime>,
    // This gate orders preference changes, quota dispatch, and file publication.
    // Probes, model resolution, and the event queue never hold it.
    generation: Mutex<u64>,
    enabled: Arc<AtomicBool>,
    queue: Mutex<Queue>,
    wake: Condvar,
    #[cfg(test)]
    notifications: std::sync::atomic::AtomicUsize,
}

#[derive(Default)]
struct Queue {
    inputs: VecDeque<(TaskEvent, f64)>,
    refresh: bool,
    stopped: bool,
}

impl Control {
    pub(super) fn start(
        config: CrossPetConfig,
        runtime: Arc<dyn CrossPetRuntime>,
    ) -> io::Result<Self> {
        let enabled = config.enabled().unwrap_or(false);
        let shared = Arc::new(Shared {
            config,
            runtime,
            generation: Mutex::default(),
            enabled: Arc::new(AtomicBool::new(enabled)),
            queue: Mutex::new(Queue {
                refresh: true,
                ..Queue::default()
            }),
            wake: Condvar::new(),
            #[cfg(test)]
            notifications: Default::default(),
        });
        let state = shared.clone();
        let worker = thread::Builder::new()
            .name("astrion-crosspet".into())
            .spawn(move || Worker::default().run(&state))?;
        Ok(Self {
            shared,
            worker: Mutex::new(Some(worker)),
        })
    }

    pub(super) fn enabled(&self) -> io::Result<bool> {
        let _gate = self.shared.lock_generation()?;
        self.shared.config.enabled()
    }

    pub(super) fn progress_flag(&self) -> Arc<AtomicBool> {
        self.shared.enabled.clone()
    }

    #[cfg(test)]
    pub(super) fn notifications(&self) -> usize {
        self.shared.notifications.load(Ordering::Relaxed)
    }

    pub(super) fn set_enabled(&self, enabled: bool) -> io::Result<()> {
        // Hold the join handle lock so a shutdown cannot leave a successful
        // enable with no observer waiting for the new preference.
        let worker = self
            .worker
            .lock()
            .map_err(|_| io::Error::other("CrossPet worker unavailable"))?;
        if !worker.as_ref().is_some_and(|handle| !handle.is_finished()) {
            return Err(io::Error::other("CrossPet worker is stopped"));
        }
        let mut generation = self.shared.lock_generation()?;
        if enabled {
            files::check_targets(&self.shared.config.destination()?)?;
        }
        self.shared.config.save_enabled(enabled)?;
        *generation = generation.wrapping_add(1);
        self.shared.enabled.store(enabled, Ordering::Release);
        if !enabled {
            self.shared.runtime.quota(false, false);
        }
        drop(generation);
        self.shared.refresh();
        Ok(())
    }

    pub(super) fn event(&self, event: TaskEvent) {
        let enabled = self.shared.enabled.load(Ordering::Acquire);
        // Keep task lifetimes available if export is enabled mid-turn. Tool and
        // model progress while disabled must not contend with the worker.
        if !enabled
            && !matches!(
                event.action,
                Action::Started
                    | Action::Finished(_)
                    | Action::Response { error: true }
                    | Action::Compression { .. }
            )
        {
            return;
        }
        let mut queue = self.shared.queue.lock().unwrap();
        if queue.stopped {
            return;
        }
        if queue.inputs.len() == EVENT_LIMIT {
            queue.inputs.pop_front();
        }
        queue.inputs.push_back((event, now()));
        if enabled {
            self.shared.notify_worker();
        }
    }

    pub(super) fn wake(&self) {
        if self.shared.enabled.load(Ordering::Acquire) {
            self.shared.refresh();
        }
    }

    pub(super) fn shutdown(&self) {
        let mut worker = self.worker.lock().unwrap();
        if let Some(worker) = worker.take() {
            self.shared.queue.lock().unwrap().stopped = true;
            self.shared.notify_worker();
            let _ = worker.join();
            self.shared.enabled.store(false, Ordering::Release);
        }
    }
}

impl Drop for Control {
    fn drop(&mut self) {
        self.shutdown();
    }
}

impl Shared {
    fn lock_generation(&self) -> io::Result<std::sync::MutexGuard<'_, u64>> {
        self.generation
            .lock()
            .map_err(|_| io::Error::other("CrossPet settings lock unavailable"))
    }

    fn refresh(&self) {
        self.queue.lock().unwrap().refresh = true;
        self.notify_worker();
    }

    fn notify_worker(&self) {
        #[cfg(test)]
        self.notifications.fetch_add(1, Ordering::Relaxed);
        self.wake.notify_one();
    }

    fn wait(&self) -> Option<(VecDeque<(TaskEvent, f64)>, bool)> {
        let queue = self.queue.lock().unwrap();
        let (mut queue, timeout) = self
            .wake
            .wait_timeout_while(queue, PROBE_INTERVAL, |queue| {
                !queue.stopped
                    && !queue.refresh
                    && (queue.inputs.is_empty() || !self.enabled.load(Ordering::Acquire))
            })
            .unwrap();
        if !timeout.timed_out() {
            (queue, _) = self
                .wake
                .wait_timeout_while(queue, COALESCE, |queue| !queue.stopped)
                .unwrap();
        }
        if queue.stopped {
            return None;
        }
        Some((
            std::mem::take(&mut queue.inputs),
            std::mem::take(&mut queue.refresh),
        ))
    }
}

struct Worker {
    instance: Uuid,
    sequence: u64,
    projection: Projection,
    last_write: Option<Publication>,
    last_probe: Option<Instant>,
    last_error: Option<Instant>,
    quota_enabled: bool,
}

impl Default for Worker {
    fn default() -> Self {
        Self {
            instance: Uuid::new_v4(),
            sequence: 0,
            projection: Projection::default(),
            last_write: None,
            last_probe: None,
            last_error: None,
            quota_enabled: false,
        }
    }
}

struct Publication {
    generation: u64,
    destination: PathBuf,
    signature: Value,
    at: Instant,
}

impl Worker {
    fn run(&mut self, shared: &Shared) {
        while let Some((inputs, refresh)) = shared.wait() {
            for (event, at) in inputs {
                self.projection.apply(event, at);
            }
            if let Err(error) = self.tick(shared, refresh) {
                let at = Instant::now();
                if self
                    .last_error
                    .is_none_or(|last| at.duration_since(last) >= LOG_INTERVAL)
                {
                    eprintln!("CrossPet export unavailable ({:?})", error.kind());
                    self.last_error = Some(at);
                }
            }
        }
        if self.quota_enabled {
            shared.runtime.quota(false, false);
        }
    }

    fn tick(&mut self, shared: &Shared, refresh: bool) -> io::Result<()> {
        let (generation, destination) = {
            let generation = shared.lock_generation()?;
            match shared
                .config
                .enabled()
                .and_then(|enabled| enabled.then(|| shared.config.destination()).transpose())
            {
                Ok(Some(destination)) => {
                    shared.enabled.store(true, Ordering::Release);
                    (*generation, destination)
                }
                disabled => {
                    shared.enabled.store(false, Ordering::Release);
                    if self.quota_enabled {
                        shared.runtime.quota(false, false);
                        self.quota_enabled = false;
                    }
                    self.last_write = None;
                    self.last_probe = None;
                    return disabled.map(|_| ());
                }
            }
        };

        let at = Instant::now();
        let stamp = now();
        if refresh
            || self
                .last_probe
                .is_none_or(|last| at.duration_since(last) >= PROBE_INTERVAL)
        {
            self.projection.inspect(shared.runtime.as_ref(), stamp);
            self.last_probe = Some(at);
        }
        self.projection.resolve_models(shared.runtime.as_ref());

        let current = shared.lock_generation()?;
        if *current != generation
            || !shared.config.enabled()?
            || shared.config.destination()? != destination
        {
            return Ok(());
        }
        self.quota_enabled = true;
        let quota = shared.runtime.quota(true, self.projection.busy());
        let signature = self
            .projection
            .snapshot(self.instance, 0, 0.0, quota.as_ref());
        if self.last_write.as_ref().is_some_and(|last| {
            last.generation == generation
                && last.destination == destination
                && last.signature == signature
                && at.duration_since(last.at) < HEARTBEAT
        }) {
            return Ok(());
        }
        // A partially published batch must not repeat a previously visible sequence.
        self.sequence += 1;
        let snapshot =
            self.projection
                .snapshot(self.instance, self.sequence, stamp, quota.as_ref());
        files::publish_snapshot(&destination, &snapshot)?;
        self.last_write = Some(Publication {
            generation,
            destination,
            signature,
            at,
        });
        Ok(())
    }
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
