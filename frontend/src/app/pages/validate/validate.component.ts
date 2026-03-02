import {
  Component,
  OnInit,
  OnDestroy,
  Inject,
  PLATFORM_ID,
  NgZone,
  ChangeDetectorRef,
  ApplicationRef,
} from '@angular/core';
import { CommonModule, isPlatformBrowser, DOCUMENT } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { DomSanitizer, SafeResourceUrl, SafeUrl } from '@angular/platform-browser';

@Component({
  standalone: true,
  selector: 'validate-page',
  imports: [CommonModule, FormsModule],
  templateUrl: './validate.component.html',
  styleUrl: './validate.component.css',
})
export class ValidatePage implements OnInit, OnDestroy {
  taskId = '';
  fileId = '';

  previewPdfUrl: SafeResourceUrl | null = null; 
  previewImgUrl: SafeUrl | null = null;         
  isPdf = false;
  isBrowser = typeof window !== 'undefined';
  drawerOpen = false;

  dataLoading = false;
  previewLoading = false;
  pollingId: any;

  // Tabs
  activeTab: 'input' | 'raw' | 'structured' = 'structured'; // Default to structured result as before
  markdownContent: string = '';
  ocrTime: number | null = null;
  structuringTime: number | null = null;

  extracted: any = {
    document_type: '',
    invoice_number: '',
    date: '',
    vendor: '',
    vendor_address: '',
    vendor_tax_id: '',
    customer_name: '',
    due_date: '',
    payment_method: '',
    total_amount: '',
    subtotal: '',
    tax_amount: '',
    currency: '',
    line_items: [] as any[],
  };

  constructor(
    private route: ActivatedRoute,
    private api: ApiService,
    private sanitizer: DomSanitizer,
    private router: Router,
    @Inject(PLATFORM_ID) private platformId: Object,
    @Inject(DOCUMENT) private document: Document,
    private appRef: ApplicationRef,  
    private zone: NgZone,
    private cdr: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    // Expand to full viewport width for the 3-column layout
    if (isPlatformBrowser(this.platformId)) {
      this.document.getElementById('app-main')?.classList.add('full-bleed');
    }
    
    this.route.paramMap.subscribe(async (pm) => {
      const t = pm?.get('taskId') ?? '';
      const f = pm?.get('fileId') ?? '';
      if (!f) return;

      this.taskId = t;
      this.fileId = f;

      this.dataLoading = true;
      try {
        await this.loadAndPopulateData();
      } finally {
        this.dataLoading = false;
        this.cdr.markForCheck();
        this.cdr.detectChanges();
      }

      // Start polling for updates (e.g. if still structuring)
      if (isPlatformBrowser(this.platformId)) {
          this.pollingId = setInterval(() => this.loadAndPopulateData(), 2000);
      }

      if (isPlatformBrowser(this.platformId)) {
        const rawUrl = this.api.fileRawUrl(this.fileId);
        const assumedPdf = /\.pdf($|\?)/i.test(rawUrl);

        this.previewLoading = true;
        try {
          await this.buildPreviewUrls(rawUrl, assumedPdf);
        } finally {
          this.previewLoading = false;
          this.cdr.markForCheck();
          this.cdr.detectChanges();
        }
      }
    });
  }

  ngOnDestroy(): void {
      if (this.pollingId) clearInterval(this.pollingId);
      // Restore normal container width for other pages
      if (isPlatformBrowser(this.platformId)) {
        this.document.getElementById('app-main')?.classList.remove('full-bleed');
      }
  }

  private async loadAndPopulateData() {
    let data: any | null = null;

    try {
      const res = await firstValueFrom(this.api.getTaskData(this.taskId));
      data = res?.data ?? null;
      this.ocrTime = res?.ocr_time ?? null;
      this.structuringTime = res?.structuring_time ?? null;
      // The new /task/data endpoint returns ocr_content directly
      if (res?.ocr_content) {
        this.markdownContent = res.ocr_content;
      }
    } catch { /* ignore */ }

    // Fallback: try /extraction/{fileId} for OCR content if not already loaded
    if (!this.markdownContent) {
      try {
        const ext = await firstValueFrom(this.api.getExtraction(this.fileId));
        if (ext?.extraction?.content) {
          this.markdownContent = ext.extraction.content;
        }
        if (!data) {
          data = ext?.extraction?.extraction_data ?? null;
        }
        if (this.ocrTime === null) {
          this.ocrTime = ext?.extraction?.extraction_data?.processing_time ?? null;
        }
      } catch { /* ignore */ }
    }

    this.zone.run(() => {
      if (data) {
        this.extracted = {
          ...this.extracted,
          ...data,
          line_items: Array.isArray(data.line_items) ? data.line_items : [],
        };
      }
      this.appRef.tick(); 
    });
  }


  private async buildPreviewUrls(rawUrl: string, assumedPdf: boolean) {
    try {
      const res = await fetch(rawUrl, { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const ct = (res.headers.get('Content-Type') || '').toLowerCase();
      const isRealPdf = ct.includes('application/pdf') || assumedPdf;
      const ab = await res.arrayBuffer();

      if (isRealPdf) {
        const blob = new Blob([ab], { type: 'application/pdf' });
        const objUrl = URL.createObjectURL(blob);
        const zoomed = objUrl + '#zoom=page-width'; 
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(objUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        const mime = ct && ct !== 'application/octet-stream' ? ct : 'image/*';
        const blob = new Blob([ab], { type: mime });
        const objUrl = URL.createObjectURL(blob);
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(objUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    } catch {
      if (assumedPdf) {
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(rawUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(rawUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    }finally {
      this.zone.run(() => { 
          this.appRef.tick(); 
      });}
  }

  async confirm() {
    try {
      await firstValueFrom(this.api.updateStructured(this.fileId, this.extracted));
      alert('Informations confirmées ✔');
      this.router.navigate(['/status']);
    } catch (e) {
      console.error(e);
      alert('Échec de la confirmation');
    }
  }
  toggleDrawer() {
    this.drawerOpen = !this.drawerOpen;
    if (this.drawerOpen && typeof window !== 'undefined') {
      setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
    }
  }


  trackByIndex = (i: number) => i;
}
