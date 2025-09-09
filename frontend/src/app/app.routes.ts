import { Routes } from '@angular/router';
import { UploadPage } from './pages/upload/upload.component';
import { StatusPage } from './pages/status/status.component';
import { ValidatePage } from './pages/validate/validate.component';

export const routes: Routes = [
  { path: '', redirectTo: 'upload', pathMatch: 'full' },   
  { path: 'upload', component: UploadPage },
  { path: 'status', component: StatusPage },
  { path: 'validate/:taskId/:fileId', component: ValidatePage },
  { path: '**', redirectTo: 'upload' },
];
