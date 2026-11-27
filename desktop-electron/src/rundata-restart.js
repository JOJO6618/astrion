// Runtime data migration copies files on the next launch. Never let that
// process begin while the previous Rust backend can still write them.
export function createRunDataRestart({ app, stopBackend, onFailure, schedule = setTimeout }) {
  let pending = false;
  return function restart() {
    if (pending) return;
    pending = true;
    // Let the backend proxy forward the accepted response before closing it down.
    schedule(async () => {
      try {
        if (await stopBackend() === false) {
          throw new Error('Rust 后端未在期限内退出');
        }
        app.relaunch();
        app.exit(0);
      } catch (error) {
        pending = false;
        onFailure(error);
      }
    }, 250);
  };
}
