---
name: remotion-best-practices
description: 使用 React 和 Remotion 制作、预览、导出视频和动画，处理字幕、地图、媒体与程序化渲染时使用。按任务加载对应参考。
version: 4.0.538
---

## Check the installed version

Read the target project's package.json, lockfile and installed Remotion types before choosing APIs. These references were collected for 4.0.538; older 4.x releases may lack Interactive, effects, HtmlInCanvas or connected composition features. Check the actual API rather than assuming all 4.x versions match, and keep Remotion packages on compatible versions. Resolve reference paths against the loaded skill directory.

## Preserve user changes

Users may make edits in the code outside of the conversation.

If you detect a surprising change made in the meanwhile, don't overwrite it, assume it was intentional or ask for confirmation.

## Creating a video

If the user asks to make, create, or build a new video or composition, load [Create a new Remotion video](./remotion-create/REFERENCE.md), whether or not a Remotion project already exists.

## New project setup

If no Remotion project currently exists, load [Create a new Remotion project](./remotion-create/REFERENCE.md)

## React Markup Best Practices

If you are writing Remotion React Markup, load [Remotion Markup Best Practices](./remotion-markup/REFERENCE.md)

## Maps

For static maps, animated routes and markers, geographic explainers, Mapbox, MapLibre, MapTiler, GeoJSON, or 3D geographic flyovers, load [Remotion Maps](./remotion-maps/REFERENCE.md).

## Multimedia

For achieving multimedia tasks in the browser, such as trimming, cropping videos, or getting metadata from them, load [Remotion Multimedia](./remotion-multimedia/REFERENCE.md)

## Improving Interactivity

By structuring the Remotion markup well, we can allow users to interactively change things in the Studio and write back to code. If relevant: [Interactivity Best Practices](./remotion-interactivity/REFERENCE.md)

## Open the preview

For exploratory composition work, start Studio as soon as the project can run so the user can inspect progress. In Astrion use a persistent terminal for the long-running server:

```bash
npx remotion studio --no-open
```

Read the actual printed address and include its complete URL in the reply so it enters Astrion's preview panel. Reuse an existing Studio when appropriate; do not guess a port or open the system browser. A composition can be visited at `/[composition-id]`, for example `http://localhost:3000/MapAnimation` when that is the actual server address.

When browser checks are available and allowed by the workspace, use the configured Playwright tools to verify Studio and the composition. If visual acceptance belongs to the user, provide the preview and specific review points. Studio WebMCP capabilities are optional and must be discovered before use.

### More options

To launch a project in Remotion Studio, open its exact local URL, or configure Studio CLI flags, load [Remotion Studio](./remotion-studio/REFERENCE.md).

## Render the video

If the original request includes a finished video, MP4 or export, render as part of that request without asking again. For a preview-only task, keep Studio available and export when requested. Choose the composition, codec, dimensions, frame rate and output path explicitly:

```bash
npx remotion render <entry-point> <composition-id> output/video.mp4
```

Inspect representative frames and verify duration, resolution and audio where tools permit. Deliver the real output using a `download://` link or `show_file`; provide editable source when requested.

For more options, see [Rendering](./remotion-render/REFERENCE.md).

For advanced rendering beyond simple `npx remotion render`, see: [Rendering Best Practices](./remotion-render/REFERENCE.md)

## Captions

When working with Captions, load [Remotion Captions](./remotion-captions/REFERENCE.md).

## Creating a SaaS, automation or application

Use the [Remotion SaaS skill](./remotion-saas/REFERENCE.md) for knowledge about Remotion-powered SaaS apps, such as `<Player>`, rendering on Lambda, Vercel, Cloudflare, via Express.js, client-side rendering, or for finding the right SaaS template.

## Looking up Remotion APIs and documentation

To find and read current Remotion documentation, load [Remotion Docs](./remotion-docs/REFERENCE.md).

## Upgrading

To upgrade Remotion, related packages, compatible Mediabunny packages, and installed Remotion Agent Skills, load [Remotion Upgrade](./remotion-upgrade/REFERENCE.md).
