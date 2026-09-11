// astrion CLI 入口：bun src/main.tsx
// 流程：BootFlow（连接 Gateway → 工作区检查/询问创建 → 创建会话）→ App（主界面）
import { createCliRenderer } from '@opentui/core';
import { createRoot, useRenderer } from '@opentui/react';
import { useState } from 'react';
import { App } from './app';
import { BootFlow, type BootResult } from './boot';

function Root() {
  const renderer = useRenderer();
  const [boot, setBoot] = useState<BootResult | null>(null);
  if (!boot) {
    return (
      <BootFlow
        cwd={process.cwd()}
        onReady={setBoot}
        onExit={(code) => {
          renderer.destroy();
          process.exit(code);
        }}
      />
    );
  }
  return <App boot={boot} />;
}

async function main() {
  // useMouse=false：不接管鼠标，拖选/复制走终端原生行为
  const renderer = await createCliRenderer({ exitOnCtrlC: false, useMouse: false });
  createRoot(renderer).render(<Root />);
}

if ((import.meta as unknown as { main?: boolean }).main) {
  void main();
}
