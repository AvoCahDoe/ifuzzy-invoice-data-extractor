import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-upload',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './upload.component.html',
  styleUrl: './upload.component.css'
})
export class UploadPage {
  selectedFile: File | null = null;
  uploading = false;
  showModal = false;
  lastFileId: string | null = null;
  selectedEngine = 'rapidocr';
  selectedPrecision = '4';
  selectedStructMode: 'regex_llm' | 'fuzzy' | 'hybrid' = 'hybrid';

  constructor(private api: ApiService, private router: Router) {}

  get showPrecisionSelector(): boolean {
    return this.selectedStructMode !== 'fuzzy';
  }

  setStructuringMode(mode: 'regex_llm' | 'fuzzy' | 'hybrid') {
    this.selectedStructMode = mode;
    if (mode === 'fuzzy') {
      this.selectedPrecision = '4';
    }
  }

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

      this.api.sendTask(fileId, false, true, this.selectedEngine, this.selectedPrecision, 1, this.selectedStructMode).subscribe({
        next: () => {},
        error: () => {}
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
