use std::{
    sync::{Arc, Condvar, Mutex},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde_json::Value;
use uuid::Uuid;

use crate::providers::{CrosspetIdentity, ProviderConfig};

use super::{CrossPetConfig, GptQuota, QuotaLimits, QuotaStatus, QuotaWindow};

const PROBE_INTERVAL: Duration = Duration::from_secs(2);
const BUSY_INTERVAL: Duration = Duration::from_secs(60);
const IDLE_INTERVAL: Duration = Duration::from_secs(120);

/// The observer reads cached, typed quota fields. A dedicated worker reads
/// credentials and checks subscription usage without holding the export gate.
pub struct SubscriptionQuota {
    shared: Arc<Shared>,
}

impl SubscriptionQuota {
    pub fn new(config: CrossPetConfig, providers: ProviderConfig) -> Self {
        Self {
            shared: Arc::new(Shared {
                config,
                providers,
                state: Mutex::default(),
                wake: Condvar::new(),
            }),
        }
    }

    pub fn snapshot(&self, enabled: bool, busy: bool) -> GptQuota {
        let mut state = self.shared.state.lock().unwrap();
        state.busy = busy;
        if state.enabled != enabled {
            state.enabled = enabled;
            state.generation += 1;
            state.packet = unavailable();
            if enabled && !state.started {
                let shared = self.shared.clone();
                state.started = thread::Builder::new()
                    .name("crosspet-quota".into())
                    .spawn(move || shared.run())
                    .is_ok();
            }
            self.shared.wake.notify_one();
        }
        state.packet.clone()
    }
}

impl Drop for SubscriptionQuota {
    fn drop(&mut self) {
        self.shared.state.lock().unwrap().stopped = true;
        self.shared.wake.notify_one();
    }
}

struct Shared {
    config: CrossPetConfig,
    providers: ProviderConfig,
    state: Mutex<State>,
    wake: Condvar,
}

struct State {
    enabled: bool,
    busy: bool,
    started: bool,
    stopped: bool,
    generation: u64,
    packet: GptQuota,
}

impl Default for State {
    fn default() -> Self {
        Self {
            enabled: false,
            busy: false,
            started: false,
            stopped: false,
            generation: 0,
            packet: unavailable(),
        }
    }
}

impl State {
    fn current(&self, generation: u64) -> bool {
        self.enabled && !self.stopped && self.generation == generation
    }
}

impl Shared {
    fn run(&self) {
        let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
        else {
            return;
        };
        let mut previous = None;
        let mut cache = Cache::default();
        while let Some(generation) = self.wait(previous) {
            if previous != Some(generation) {
                cache = Cache::default();
                previous = Some(generation);
            }
            self.refresh(&runtime, generation, &mut cache);
        }
    }

    fn wait(&self, previous: Option<u64>) -> Option<u64> {
        let state = self.state.lock().unwrap();
        let (state, _) = self
            .wake
            .wait_timeout_while(state, PROBE_INTERVAL, |state| {
                previous.is_some_and(|generation| state.current(generation))
            })
            .unwrap();
        let state = self
            .wake
            .wait_while(state, |state| !state.enabled && !state.stopped)
            .unwrap();
        (!state.stopped).then_some(state.generation)
    }

    fn refresh(&self, runtime: &tokio::runtime::Runtime, generation: u64, cache: &mut Cache) {
        if !self.config.enabled().unwrap_or(false) {
            return;
        }
        let identity = self.providers.crosspet_account();
        {
            let mut state = self.state.lock().unwrap();
            if !state.current(generation) {
                return;
            }
            match identity {
                Ok(identity) => cache.update_identity(identity, &mut state.packet),
                Err(_) => {
                    mark_unavailable(&mut state.packet);
                    return;
                }
            }
        }
        let Some(identity) = cache.identity.as_ref() else {
            return;
        };
        {
            let state = self.state.lock().unwrap();
            if !state.current(generation)
                || !cache
                    .timing
                    .begin(Instant::now(), state.busy, state.packet.at.is_some())
            {
                return;
            }
        }

        if !self.config.enabled().unwrap_or(false) {
            return;
        }
        let usage = runtime
            .block_on(self.providers.crosspet_usage_detailed(identity))
            .map(|(current, result)| {
                (
                    current,
                    result.ok().and_then(|value| normalize_usage(&value)),
                )
            });
        if !self.config.enabled().unwrap_or(false) {
            return;
        }
        // On failure, distinguish a temporary outage from logout or replacement.
        let observed = usage.is_err().then(|| self.providers.crosspet_account());
        let mut state = self.state.lock().unwrap();
        if !state.current(generation) {
            return;
        }
        match usage {
            Ok((current, limits)) => {
                // OAuth may rotate the credential during this request. The
                // provider confirms which version safely belongs to the response.
                cache.identity = Some(current);
                if let Some(limits) = limits {
                    state.packet.status = QuotaStatus::Live;
                    state.packet.at = Some(
                        SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs_f64(),
                    );
                    state.packet.rate_limits = Some(limits);
                } else {
                    mark_unavailable(&mut state.packet);
                }
            }
            Err(_) => match observed {
                Some(Ok(current)) if current != cache.identity => {
                    cache.update_identity(current, &mut state.packet);
                }
                _ => mark_unavailable(&mut state.packet),
            },
        }
    }
}

#[derive(Default)]
struct Cache {
    identity: Option<CrosspetIdentity>,
    timing: Timing,
}

impl Cache {
    fn update_identity(&mut self, identity: Option<CrosspetIdentity>, packet: &mut GptQuota) {
        if self.identity == identity {
            return;
        }
        self.identity = identity;
        self.timing = Timing::default();
        *packet = if self.identity.is_some() {
            GptQuota {
                connected: true,
                status: QuotaStatus::Checking,
                epoch: Some(Uuid::new_v4()),
                ..unavailable()
            }
        } else {
            unavailable()
        };
    }
}

#[derive(Default)]
struct Timing {
    last: Option<(Instant, Duration)>,
}

impl Timing {
    fn begin(&mut self, now: Instant, busy: bool, has_sample: bool) -> bool {
        if let Some((last, wait)) = &mut self.last {
            if busy && has_sample {
                *wait = BUSY_INTERVAL;
            }
            if now.duration_since(*last) < *wait {
                return false;
            }
        }
        self.last = Some((now, if busy { BUSY_INTERVAL } else { IDLE_INTERVAL }));
        true
    }
}

fn unavailable() -> GptQuota {
    GptQuota {
        connected: false,
        status: QuotaStatus::Unavailable,
        epoch: None,
        at: None,
        rate_limits: None,
    }
}

fn mark_unavailable(packet: &mut GptQuota) {
    packet.status = if packet.rate_limits.is_some() {
        QuotaStatus::Stale
    } else {
        QuotaStatus::Unavailable
    };
}

fn normalize_usage(value: &Value) -> Option<QuotaLimits> {
    let limits = value.get("rate_limit")?;
    let window = |key| {
        let raw = limits.get(key)?;
        let window = QuotaWindow {
            used_percent: raw.get("used_percent")?.as_f64()?,
            window_duration_mins: raw.get("limit_window_seconds")?.as_f64()? / 60.0,
            resets_at: raw.get("reset_at")?.as_f64()?,
        };
        window.valid().then_some(window)
    };
    let primary = window("primary_window");
    let secondary = window("secondary_window");
    (primary.is_some() || secondary.is_some()).then_some(QuotaLimits { primary, secondary })
}

#[cfg(test)]
#[path = "../../../test/2026-11-25_RustCrossPet/quota_contract.rs"]
mod tests;
