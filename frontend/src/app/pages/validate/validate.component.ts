import {
  Component,
  OnInit,
  OnDestroy,
  Inject,
  PLATFORM_ID,
  NgZone,
  ChangeDetectorRef,
  ApplicationRef,
  HostListener,
} from '@angular/core';
import { CommonModule, isPlatformBrowser, DOCUMENT } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { DomSanitizer, SafeResourceUrl, SafeUrl } from '@angular/platform-browser';
import { ZoomableImageViewerComponent } from '../../components/zoomable-image-viewer/zoomable-image-viewer.component';

@Component({
  standalone: true,
  selector: 'validate-page',
  imports: [CommonModule, FormsModule, ZoomableImageViewerComponent],
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
  filename = '';

  dataLoading = false;
  previewLoading = false;
  pollingId: any;
  private blobUrl: string | null = null;

  imageModalOpen = false;

  // Tabs
  activeTab: 'input' | 'raw' | 'structured' = 'structured'; // Default to structured result as before
  markdownContent: string = '';
  ocrTime: number | null = null;
  structuringTime: number | null = null;

  // Input type: pdf_digital, pdf_scanned, image, other
  inputType: string | null = null;

  extracted: any = {
    document_type: '',
    invoice_number: '',
    date: '',
    vendor_name: '',
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

  validationErrors: Record<string, string> = {};
  validationWarnings: Record<string, string> = {};

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
        const assumedPdf = /\.pdf($|\?)/i.test(this.filename || '');

        this.previewLoading = true;
        try {
          await this.buildPreviewUrls(assumedPdf);
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
      if (this.blobUrl && isPlatformBrowser(this.platformId)) {
        URL.revokeObjectURL(this.blobUrl);
      }
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
      this.inputType = res?.input_type ?? null;
      if (res?.filename) this.filename = res.filename;
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
        // Map backend fields (vendor_name) and ensure line_items structure
        const items = Array.isArray(data.line_items) ? data.line_items : [];
        this.extracted = {
          document_type: data.document_type ?? '',
          invoice_number: data.invoice_number ?? '',
          date: data.date ?? '',
          vendor_name: data.vendor_name ?? data.vendor ?? '',
          vendor_address: data.vendor_address ?? '',
          vendor_tax_id: data.vendor_tax_id ?? '',
          customer_name: data.customer_name ?? '',
          due_date: data.due_date ?? '',
          payment_method: data.payment_method ?? '',
          total_amount: data.total_amount != null ? String(data.total_amount) : '',
          subtotal: data.subtotal != null ? String(data.subtotal) : '',
          tax_amount: data.tax_amount != null ? String(data.tax_amount) : '',
          currency: data.currency ?? '',
          line_items: items.map((li: any) => ({
            description: li.description ?? '',
            quantity: li.quantity != null ? li.quantity : null,
            unit_price: li.unit_price != null ? li.unit_price : null,
            total_price: li.total_price != null ? li.total_price : null,
          })),
        };
      }
      this.appRef.tick();
    });
  }


  private async buildPreviewUrls(assumedPdf: boolean) {
    try {
      const blob = await firstValueFrom(this.api.getFileBlob(this.fileId));
      const ct = blob.type || '';
      const isRealPdf = ct.includes('application/pdf') || assumedPdf;

      if (this.blobUrl) {
        URL.revokeObjectURL(this.blobUrl);
        this.blobUrl = null;
      }

      if (isRealPdf) {
        const objUrl = URL.createObjectURL(blob);
        this.blobUrl = objUrl;
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(objUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        const objUrl = URL.createObjectURL(blob);
        this.blobUrl = objUrl;
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(objUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    } catch {
      const rawUrl = this.api.fileRawUrl(this.fileId);
      if (assumedPdf) {
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(rawUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(rawUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    } finally {
      this.zone.run(() => {
        this.appRef.tick();
      });
    }
  }

  validateFields(): boolean {
    this.validationErrors = {};
    this.validationWarnings = {};
    const e = this.extracted;

    if (!(e.vendor_name || '').trim()) {
      this.validationErrors['vendor_name'] = 'Vendor name is required';
    }
    if (!(e.customer_name || '').trim()) {
      this.validationErrors['customer_name'] = 'Customer name is required';
    }
    if (!(e.total_amount || '').toString().trim()) {
      this.validationErrors['total_amount'] = 'Total amount is required';
    }
    const totalNum = parseFloat(String(e.total_amount || '0').replace(/[,\s]/g, ''));
    if (isNaN(totalNum) || totalNum < 0) {
      this.validationErrors['total_amount'] = this.validationErrors['total_amount'] || 'Enter a valid amount';
    }
    const dateStr = (e.date || '').trim();
    if (dateStr && !/^\d{4}-\d{2}-\d{2}$|^\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}$/.test(dateStr)) {
      this.validationErrors['date'] = 'Use format YYYY-MM-DD or DD/MM/YYYY';
    }
    const dueDateStr = (e.due_date || '').trim();
    if (dueDateStr && !/^\d{4}-\d{2}-\d{2}$|^\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}$/.test(dueDateStr)) {
      this.validationErrors['due_date'] = 'Use format YYYY-MM-DD or DD/MM/YYYY';
    }
    const invoiceNumber = (e.invoice_number || '').trim();
    if (!invoiceNumber) {
      this.validationWarnings['invoice_number'] = 'Invoice number is missing';
    } else if (invoiceNumber.length < 3) {
      this.validationWarnings['invoice_number'] = 'Invoice number looks too short';
    }
    const currency = (e.currency || '').trim();
    if (!currency) {
      this.validationWarnings['currency'] = 'Currency is empty';
    }
    if (e.line_items?.length) {
      e.line_items.forEach((item: any, i: number) => {
        const q = item.quantity;
        const up = item.unit_price;
        const tp = item.total_price;
        if (q != null && up != null && tp != null) {
          const expected = Number(q) * Number(up);
          const actual = Number(tp);
          if (Math.abs(expected - actual) > 0.02) {
            this.validationErrors[`line_${i}`] = `Row ${i + 1}: quantity × unit price should equal total`;
          }
        }
      });
      const totalNumValue = Number(String(e.total_amount || '').replace(/[,\s]/g, ''));
      const lineTotal = e.line_items.reduce((sum: number, item: any) => sum + Number(item?.total_price || 0), 0);
      if (!isNaN(totalNumValue) && totalNumValue > 0 && Math.abs(lineTotal - totalNumValue) > 1) {
        this.validationWarnings['line_items_total'] = 'Line items total does not match invoice total';
      }
    }
    const subtotalNum = Number(String(e.subtotal || '').replace(/[,\s]/g, ''));
    const taxNum = Number(String(e.tax_amount || '').replace(/[,\s]/g, ''));
    if (!isNaN(subtotalNum) && !isNaN(taxNum) && !isNaN(totalNum) && subtotalNum >= 0 && taxNum >= 0) {
      if (Math.abs((subtotalNum + taxNum) - totalNum) > 1) {
        this.validationWarnings['totals_math'] = 'Subtotal + tax does not match total amount';
      }
    }
    return Object.keys(this.validationErrors).length === 0;
  }

  async confirm() {
    if (!this.validateFields()) {
      return;
    }
    try {
      await firstValueFrom(this.api.updateStructured(this.fileId, this.extracted, this.taskId));
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

  addLineItem() {
    if (!Array.isArray(this.extracted.line_items)) {
      this.extracted.line_items = [];
    }
    this.extracted.line_items.push({
      description: '',
      quantity: null,
      unit_price: null,
      total_price: null,
    });
  }

  removeLineItem(index: number) {
    if (!Array.isArray(this.extracted.line_items)) return;
    this.extracted.line_items.splice(index, 1);
  }

  get validationErrorList(): string[] {
    return Object.values(this.validationErrors);
  }

  get validationWarningList(): string[] {
    return Object.values(this.validationWarnings);
  }

  openImageModal() {
    this.imageModalOpen = true;
  }

  closeImageModal() {
    this.imageModalOpen = false;
  }

  @HostListener('document:keydown.escape')
  onEscape() {
    if (this.imageModalOpen) this.closeImageModal();
  }
}
