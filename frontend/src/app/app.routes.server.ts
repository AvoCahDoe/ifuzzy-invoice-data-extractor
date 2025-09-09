import { RenderMode, ServerRoute } from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
  { path: 'validate/:taskId/:fileId', getPrerenderParams: () => Promise.resolve([] as Record<string, string>[]), renderMode: RenderMode.Prerender },
  { path: '**', renderMode: RenderMode.Server }
];
