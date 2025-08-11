import { ApplicationConfig, NgModule } from '@angular/core';
import { provideServerRendering } from '@angular/platform-server';
import { provideHttpClient } from '@angular/common/http';
import { PdfViewerModule } from 'ng2-pdf-viewer';

// @NgModule({
//   imports: [PdfViewerModule]
// })

export const appConfig: ApplicationConfig = {
  providers: [
    provideServerRendering(),
    provideHttpClient()
  ]
};
