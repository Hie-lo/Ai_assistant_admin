# نمونه فایل‌های محصولات — Samples for Source Mapping

این پوشه نمونه فایل‌های Excel برای تست جریان نگاشت و Import طبق `SOURCE_SYNC_DOMAIN_SPECIFICATION_V1` است.

## فایل‌ها

1. **products_sample_fa.xlsx** — استاندارد فارسی (توصیه شده per spec بخش 3)
   - هدرها: شناسه منبع، نام محصول، قیمت، موجودی، دسته بندی، توضیحات، کد کالا، بارکد، تصویر، ارز
   - 4 محصول نمونه

2. **products_sample_en.xlsx** — استاندارد انگلیسی
   - هدرها: external_id, name, price, stock, category, description, sku, barcode, image1, currency
   - تست alias انگلیسی

3. **products_sample_mixed_custom.xlsx** — ترکیبی + فیلدهای سفارشی
   - هدرها: کد محصول، عنوان، قیمت تومان، تعداد، گروه، رنگ، سایز، وزن، توضیحات، عکس اصلی، شناسه
   - رنگ، سایز، وزن → باید به CUSTOM نگاشت شوند (per spec بخش 6: Unknown columns are inert until explicitly enabled)
   - تست می‌کند که سیستم فیلدهای سفارشی را حفظ کند

4. **products_sample_nonstandard.xlsx** — غیراستاندارد / messy
   - هدرها: Product, Price (Toman), Count, My Custom Note, Photo, Item Code, Extra Info
   - تست heuristic mapping: Product→name, Price (Toman)→price, Count→stock, Photo→image1, Item Code→sku
   - My Custom Note, Extra Info → CUSTOM
   - per دستور کار: سیستم باید با منابع غیراستاندارد هم کار کند

5. **products_sample_edge_cases.xlsx** — لبه‌ها و خطاها
   - هدر تکراری: قیمت دوبار
   - سطر خالی
   - قیمت نامعتبر (not-a-number) → باید INVALID شود نه کل sync fail (spec بخش 18: Error isolation)
   - موجودی خالی

## آیا فایل متفاوت مشکلی دارد؟

**خیر — دقیقاً طبق دستور کار طراحی شده:**

- spec بخش 3: نمونه پیشنهادی است، نه اجباری — سیستم باید با منابع غیراستاندارد هم کار کند
- spec بخش 6: ستون‌های ناشناخته تا زمانی که صریحاً نگاشت نشوند، inert هستند؛ ستون‌های غیرفعال از ingestion حذف می‌شوند؛ هدرهای تکراری نیاز به disambiguation دارند
- spec بخش 10: Row number و ordering هرگز identity نیست — جابجایی سطرها مشکلی ایجاد نمی‌کند
- spec بخش 18: خطای سطح سطر نباید سطرهای نامربوط را fail کند

بنابراین هر فایلی با هر ترتیبی از ستون‌ها کار می‌کند، به شرطی که حداقل یک ستون به `name` نگاشت شود و نگاشت فعال شود.

## جریان تست

1. منبع EXCEL_UPLOAD بساز
2. در جزئیات منبع → «کشف ستون‌ها و پیشنهاد نگاشت» → فایل نمونه را آپلود کن
3. در ویرایشگر نگاشت، پیشنهاد هوشمند را ببین، تصحیح کن، ذخیره (DRAFT)
4. فعال‌سازی (ACTIVE)
5. پیش‌نمایش (Dry-Run) → Diff
6. Import نهایی → محصولات

## Google Sheets

برای Google Sheets، همین ساختار را در شیت پیاده کن. external_ref = Spreadsheet ID، sheet_name = نام شیت.
