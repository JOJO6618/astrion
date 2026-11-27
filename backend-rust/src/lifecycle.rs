use astrion_headless::{catalog::CatalogSource, runtime::{ModelConfig, Runtime}, schedules::ScheduleService};
use axum::Router;
use std::{io, net::SocketAddr, time::Duration};
use tokio::{net::TcpListener, sync::watch};

pub async fn serve(
    listener: TcpListener,
    router: Router,
    runtime: Runtime,
    schedules: ScheduleService,
    notice_catalog: CatalogSource,
    notice_model: Option<ModelConfig>,
) -> io::Result<()> {
    #[cfg(unix)]
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let owner = schedules.acquire_owner().map_err(io::Error::other)?;
    let (stop, receiver) = watch::channel(false);
    let notice_worker = tokio::spawn(run_background_notices(runtime.clone(), notice_catalog, notice_model, receiver.clone()));
    let scheduler = tokio::spawn(owner.run(receiver));
    let signal_stop = stop.clone();
    let signal = async move {
        #[cfg(unix)]
        tokio::select! {
            _ = terminate.recv() => (),
            _ = tokio::signal::ctrl_c() => (),
            _ = crate::desktop_stop::desktop_stop() => (),
        }
        #[cfg(not(unix))]
        tokio::select! {
            _ = tokio::signal::ctrl_c() => (),
            _ = crate::desktop_stop::desktop_stop() => (),
        }
        signal_stop.send_replace(true);
    };
    let result = axum::serve(
        listener,
        router.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(signal)
    .await;
    // At this point all admitted HTTP requests have returned. Wait for any
    // scheduled or notice admission before taking the final task snapshot.
    stop.send_replace(true);
    let scheduler_result = scheduler.await;
    let notice_result = notice_worker.await;
    let drained = drain(&runtime).await;
    runtime.shutdown_process_resources().map_err(io::Error::other)?;
    runtime.shutdown_mcp_resources().await.map_err(io::Error::other)?;
    drained?;
    notice_result.map_err(io::Error::other)?;
    scheduler_result
        .map_err(io::Error::other)?
        .map_err(io::Error::other)?;
    result
}

async fn run_background_notices(
    runtime: Runtime, catalog: CatalogSource, fixed: Option<ModelConfig>, mut stop: watch::Receiver<bool>,
) {
    let mut ticks = tokio::time::interval(Duration::from_secs(1));
    ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            biased;
            _ = stop.changed() => return,
            _ = ticks.tick() => {
                let (runtime, catalog, fixed) = (runtime.clone(), catalog.clone(), fixed.clone());
                let _ = tokio::task::spawn_blocking(move || runtime.poll_idle_process_notices(&catalog, fixed.as_ref())).await;
            }
        }
    }
}

async fn drain(runtime: &Runtime) -> io::Result<()> {
    runtime.stop_task_admission().map_err(io::Error::other)?;
    let active = runtime.navigation_runs().map_err(io::Error::other)?;
    for task in active.into_iter().filter(|run| run.running) {
        runtime
            .cancel_task(&task.workspace_id, &task.task_id)
            .map_err(io::Error::other)?;
    }
    while runtime
        .navigation_runs()
        .map_err(io::Error::other)?
        .iter()
        .any(|task| task.running)
    {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    Ok(())
}
