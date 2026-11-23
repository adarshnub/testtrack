# BankLedger Pro

Statement Studio is the deployable demonstration. Upload a bank-statement PDF, review extracted transactions and balance checks, then download an Excel workbook with source references and exceptions.

## Running locally

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r apps/extractor/requirements-dev.txt
$env:PYTHONPATH = 'apps/extractor'
.venv/Scripts/python.exe -m uvicorn demo_local:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open `http://127.0.0.1:8000` and select **Use sample PDF**. It is a synthetic six-transaction statement. On Linux or macOS, activate `.venv/bin/activate`, set `PYTHONPATH` with `export`, and use `python`.

To enable the optional outlined-statement profile locally, install the legacy engine and pinned English data, then restart the server in the configured shell.

```powershell
uv pip install --python .venv/Scripts/python.exe tesserocr==2.13.0
python scripts/fetch_legacy_ocr_data.py var/legacy-tessdata
$env:BANKLEDGER_TESSDATA_DIR = (Resolve-Path var/legacy-tessdata).Path
```

## Tests

```powershell
$env:PYTHONPATH = 'apps/extractor'
.venv/Scripts/python.exe -m pytest apps/extractor/tests
node --check apps/extractor/demo_web/demo.js
```

## AWS deployment

The demo uses Python 3.12 on AWS Lambda with a Function URL. Use an explicitly configured AWS CLI profile. The deployment creates a dedicated Lambda and IAM execution role and a CloudWatch log group with seven-day retention. It reserves two concurrent executions when the account quota permits. Anyone with the URL can open the dashboard, load the synthetic example, or convert their own statement and download the workbook.

```powershell
uv pip install --no-cache --target var/demo-lambda-deps --python-version 3.12 --python-platform x86_64-manylinux_2_28 --only-binary ':all:' PyMuPDF==1.28.2 pdfplumber==0.11.11 pydantic==2.14.4 openpyxl==3.1.5 defusedxml==0.7.1
python scripts/deploy_demo.py --profile personal --region ap-south-1
```

For the outlined-statement profile, deploy the optional Linux legacy OCR dependencies with the application.

```powershell
uv pip install --no-cache --no-deps --target var/demo-legacy-ocr --python-version 3.12 --python-platform x86_64-manylinux_2_28 --only-binary ':all:' tesserocr==2.13.0
python scripts/fetch_legacy_ocr_data.py var/demo-legacy-ocr/tessdata
python scripts/deploy_demo.py --profile personal --region ap-south-1 --stage-in-s3 --legacy-ocr-dependencies var/demo-legacy-ocr
```

The S3 option stages the application ZIP in a private bucket in the deployment region and deletes the artifact after the deployment attempt.

PDF bytes, parsed rows, and workbook bytes are processed in request memory. App logs record generic failures only. The workbook stays in browser memory until the page is refreshed or closed. Remove the function URL from the Lambda configuration to stop public access when the demo ends.

## Supported inputs and review workbook

- One PDF, up to 4 MB, 40 pages, and 1,800 extracted rows.
- For searchable PDFs, the service reads positioned words and identifies supported US-style debit/credit, section-based, and signed-amount tables. The original statement with selectable text gives the widest format coverage.
- For the validated six-page outlined layout, the optional fallback runs Tesseract's classic non-neural engine only on known printed statement regions. It locates section headings and subtotals when tables move vertically between statements. Each render must reconcile against the printed balances, amounts, and counts. Two resolutions must agree on all accounting fields before a workbook is offered. Recovered text is flagged for review. Scanned-image statements, other outlined layouts, and original cheque images are unsupported.
- The browser workspace displays the actual Excel workbook, starting at **Statement Register**. Select a sheet tab, cell, row or column. Double-click a cell or use the formula bar to edit, then choose **Download Excel** to save the changes. Edits stay in the browser and apply to worksheet cells. The statement reconciliation shown above the workbook reflects the original extraction. Excel recalculates formulas when the workbook opens.
- The first tab uses the Book11 register layout through **Credit**, with the reference's column offsets, date and amount formats, and debit and credit totals. Balances, dates of clearing and source evidence are available in the supporting reconciliation, transaction and check tabs. Review workbooks remain available when verification fails and explain observed differences.
- The demo fills **Split** from the saved company assignments in `apps/extractor/exporters/company_splits.json`. Company names match by full phrase with case, spacing, and punctuation normalized. An unknown or conflicting match leaves **Split** empty. Verify the counterpart account on transfers.
- **Name** and **Split** use reviewed company aliases, supported narration, and exact transfer-account identifiers. Book11 assignments that cannot be inferred from the narration apply to the verified original PDF only. They are keyed by the file fingerprint and the uniquely aligned statement row. A new statement with ambiguous narration or a bare check stays open for manual review.
- For **Georgia ITS Tax**, the register's Credit amount selects the split. Below 20 goes to **Wireless Payments**, 20 through 300 goes to **Business License and Permits**, and above 300 goes to **Sales Tax Payable**.
- Physical checks retain the bank clearing date and leave the written and accounting dates blank.
- Client management, persistent mapping rules, prior-register uploads, QuickBooks import, and IIF downloads are outside the deployed demo. The repository retains the earlier accounting service and full-stack scaffolding for future development.
