// Executed only in the isolated trusted fixture, capturing only its own native window.
async function recordDiagnosticWindow(sourceId) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { mandatory: { chromeMediaSource: 'desktop', chromeMediaSourceId: sourceId, maxFrameRate: 30 } }
  });
  const video = document.createElement('video');
  video.muted = true;
  video.srcObject = stream;
  await video.play();
  const canvas = document.createElement('canvas');
  canvas.width = 640;
  canvas.height = 400;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  const recording = { frames: [], greenFrames: [], stopped: false, stream };
  window.surfaceRecording = recording;
  const next = () => {
    if (recording.stopped) return;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
    let green = 0;
    for (let y = 60; y < canvas.height; y += 2) {
      for (let x = 0; x < canvas.width; x += 2) {
        const i = (y * canvas.width + x) * 4;
        const r = data[i], g = data[i + 1], b = data[i + 2];
        if (g > r + 8 && g > b + 8 && g > 20) green++;
      }
    }
    const row = { ms: performance.now(), green, theme: document.documentElement.dataset.theme, state: window.surfaceDiag.inspect() };
    recording.frames.push(row);
    if (green > 200 && recording.greenFrames.length < 8) recording.greenFrames.push({ ...row, png: canvas.toDataURL('image/png') });
    video.requestVideoFrameCallback(next);
  };
  video.requestVideoFrameCallback(next);
  return { started: true, settings: stream.getVideoTracks()[0].getSettings() };
}
