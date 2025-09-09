import 'zone.js'; 
import { bootstrapApplication, provideClientHydration, withEventReplay } from '@angular/platform-browser';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { App } from './app/app';
import { routes } from './app/app.routes';

bootstrapApplication(App, {
  providers: [
    provideRouter(routes),
    provideHttpClient(withFetch()), provideClientHydration(withEventReplay()),
  ],
}).catch((err: unknown) => console.error(err));
