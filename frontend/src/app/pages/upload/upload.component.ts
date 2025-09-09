import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-upload',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './upload.component.html',
  styleUrl: './upload.component.css'
})
export class UploadPage {
  selectedFile: File | null = null;
  uploading = false;
  showModal = false;
  lastFileId: string | null = null;

  constructor(private api: ApiService, private router: Router) {}

  triggerFileInput() {
    const input = document.getElementById('file-upload') as HTMLInputElement | null;
    input?.click();
  }

  onDragOver(evt: DragEvent) {
    evt.preventDefault();
  }

  onDrop(evt: DragEvent) {
    evt.preventDefault();
    const file = evt.dataTransfer?.files?.[0];
    if (file) this.start(file);
  }

  async onFileChange(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) this.start(file);
  }

  private clearFileInput() {
    const input = document.getElementById('file-upload') as HTMLInputElement | null;
    if (input) input.value = '';
  }

  private async start(file: File) {
    this.selectedFile = file;
    this.uploading = true;

    try {
      const up: any = await firstValueFrom(this.api.upload(file));
      const fileId = up?.file_id as string;
      this.lastFileId = fileId;

      this.showModal = true;
      this.uploading = false;
      this.selectedFile = null;
      this.clearFileInput();

      this.api.sendTask(fileId, false, true).subscribe({
        next: () => {
        },
        error: () => {
          this.api.process(fileId, false).subscribe({
            next: () => {
              this.api.structure(fileId).subscribe({
                next: () => {},
                error: () => {}
              });
            },
            error: () => {}
          });
        }
      });
    } catch (e) {
      console.error(e);
      this.uploading = false;
      alert('Échec du téléversement.');
    }
  }


  closeModal() { this.showModal = false; }

  goToStatus() {
    this.showModal = false;
    this.router.navigate(['/status']);
  }
}
