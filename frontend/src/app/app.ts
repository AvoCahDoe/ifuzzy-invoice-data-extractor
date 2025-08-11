import { Component, ChangeDetectorRef, Inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { CommonModule, isPlatformBrowser } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PLATFORM_ID } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  selectedFile: File | null = null;
  filePreviewUrl: SafeResourceUrl | null = null; // 👈 sécurisé
  isPdf = false;
  isImage = false;
  loading = false;
  fileId: string | null = null;
  sidebarCollapsed = false;
  isBrowser = false;

  extracted = {
    document_type: '',
    currency: '',
    payment_method: '',
    invoice_number: '',
    invoice_date: '',
    due_date: '',
    total_amount: '',
    tax_amount: '',
    line_items: [] as {
      name: string;
      quantity: string;
      unit_price: string;
      packaging: string;
      unit: string;
      total_ht: string;
    }[]
  };

  constructor(
    private http: HttpClient,
    private cd: ChangeDetectorRef,
    @Inject(PLATFORM_ID) private platformId: Object,
    private sanitizer: DomSanitizer // 👈 injection
  ) {
    this.isBrowser = isPlatformBrowser(this.platformId);
  }

  toggleSidebar() {
    this.sidebarCollapsed = !this.sidebarCollapsed;
  }

  triggerFileInput() {
    const input = document.getElementById('file-upload') as HTMLInputElement;
    if (input) input.click();
  }

  onFileChange(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input?.files?.[0];
    if (!file) return;

    this.selectedFile = file;
    this.isPdf = file.type === 'application/pdf';
    this.isImage = file.type.startsWith('image/');

    if (this.isImage) {
      const reader = new FileReader();
      reader.onload = () => {
        this.filePreviewUrl = this.sanitizer.bypassSecurityTrustResourceUrl(reader.result as string);
        this.cd.detectChanges();
      };
      reader.readAsDataURL(file);
    } else if (this.isPdf && this.isBrowser) {
      const blobUrl = URL.createObjectURL(file);
      this.filePreviewUrl = this.sanitizer.bypassSecurityTrustResourceUrl(blobUrl); // 👈 sanitize
      this.cd.detectChanges();
    }
  }

  async onSubmit() {
    if (!this.selectedFile) return;

    this.loading = true;
    this.cd.detectChanges();

    const formData = new FormData();
    formData.append('file', this.selectedFile);

    try {
      const uploadRes: any = await this.http.post('http://localhost:8000/upload', formData).toPromise();
      this.fileId = uploadRes.file_id;

      await this.http.post(`http://localhost:8000/process/${this.fileId}`, {}).toPromise();
      const structureRes: any = await this.http.post(`http://localhost:8000/structure/${this.fileId}`, {}).toPromise();

      this.extracted = structureRes.data;
    } catch (err) {
      console.error('Erreur pendant le pipeline:', err);
    } finally {
      this.loading = false;
      this.cd.detectChanges();
    }
  }

  async onConfirmEdit() {
    if (!this.fileId) {
      alert("Aucun fichier traité à mettre à jour.");
      return;
    }

    try {
      await this.http.put(
        `http://localhost:8000/update/${this.fileId}`,
        this.extracted
      ).toPromise();

      alert('✅ Modifications enregistrées avec succès.');

      this.selectedFile = null;
      this.filePreviewUrl = null;
      this.isPdf = false;
      this.isImage = false;
      this.fileId = null;
      this.extracted = {
        document_type: '',
        currency: '',
        payment_method: '',
        invoice_number: '',
        invoice_date: '',
        due_date: '',
        total_amount: '',
        tax_amount: '',
        line_items: []
      };
      this.cd.detectChanges();

    } catch (error) {
      console.error('Erreur lors de la mise à jour :', error);
      alert('❌ Échec de la mise à jour.');
    }
  }
}
