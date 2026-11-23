"use strict";

const element = (id) => document.getElementById(id);
const form = element("convert-form");
const picker = element("statement");
const dropZone = element("drop-zone");
const feedback = element("feedback");
const convertButton = element("convert-button");
const sampleButton = element("use-sample");
const results = element("result");
const downloadButton = element("download");
const editor = new WorkbookGrid(element("workbook-editor"));
const maxUpload = 4_000_000;
let selectedFile = null;
let busy = false;

function message(text, error = false) {
  feedback.textContent = text;
  feedback.classList.toggle("error", error);
}

function setBusy(value) {
  busy = value;
  picker.disabled = value;
  sampleButton.disabled = value;
  convertButton.disabled = value;
  form.setAttribute("aria-busy", String(value));
}

function selectFile(file) {
  selectedFile = null;
  element("file-title").textContent = "Choose a PDF or drag it here";
  message("");
  if (!file) return;
  if (!/\.pdf$/i.test(file.name)) return message("Choose a bank statement with a .pdf extension.", true);
  if (!file.size || file.size > maxUpload) return message("Choose a nonempty PDF smaller than 4 MB.", true);
  selectedFile = file;
  element("file-title").textContent = file.name;
  message("Statement selected.");
}

picker.addEventListener("change", () => selectFile(picker.files[0]));
for (const action of ["dragenter", "dragover"]) {
  dropZone.addEventListener(action, (event) => {
    event.preventDefault();
    if (!busy) dropZone.classList.add("dragging");
  });
}
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  if (busy) return;
  if (event.dataTransfer.files.length !== 1) return message("Drop one statement PDF at a time.", true);
  picker.value = "";
  selectFile(event.dataTransfer.files[0]);
});

sampleButton.addEventListener("click", async () => {
  setBusy(true);
  message("Loading the synthetic statement…");
  try {
    const response = await fetch("/sample.pdf");
    if (!response.ok) throw new Error("The sample could not be loaded. Please try again.");
    picker.value = "";
    selectFile(new File([await response.blob()], "bankledger-synthetic-statement.pdf", { type: "application/pdf" }));
  } catch (error) {
    message(error.message, true);
  } finally {
    setBusy(false);
  }
});

function money(value) {
  if (value == null) return "—";
  const [amount, cents] = value.replace(/^-/, "").split(".");
  const grouped = amount.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${value.startsWith("-") ? "−" : ""}${grouped}.${cents ?? "00"}`;
}

function addText(parent, tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  parent.append(item);
  return item;
}

function showResult(data, file) {
  const rec = data.reconciliation;
  const period = data.periodStart && data.periodEnd ? `${data.periodStart} to ${data.periodEnd}` : "Statement period unverified";
  element("statement-meta").textContent = [
    data.bank || "Bank unidentified", data.last4 ? `Account •••• ${data.last4}` : null, period,
    `${data.pages} ${data.pages === 1 ? "page" : "pages"}`,
  ].filter(Boolean).join(" · ");
  const summary = element("summary-grid");
  summary.replaceChildren();
  for (const [label, value] of [
    ["Opening balance", rec.opening], ["Total credits", rec.credits], ["Total debits", rec.debits],
    ["Printed closing", rec.expected], ["Balance difference", rec.difference],
  ]) {
    const card = addText(summary, "div", "");
    addText(card, "span", label);
    addText(card, "strong", money(value));
  }
  element("reconciliation").classList.toggle("passed", rec.status === "PASSED");
  element("reconciliation-label").textContent = rec.status === "PASSED" ? "Bank balances verified" :
    rec.status === "FAILED" ? "Balance check failed" : "Verification incomplete";
  element("reconciliation-detail").textContent = rec.status === "PASSED" ?
    (data.recoveredText ? "Printed totals agree. Compare the text recovered by classic OCR with the original statement." :
      "Calculated closing balance agrees with the bank statement.") :
    "Review the source statement and the workbook’s exceptions before relying on these rows.";
  const issues = element("issues");
  issues.replaceChildren();
  for (const issue of rec.issues) addText(issues, "li", issue.page ? `Page ${issue.page}: ${issue.message}` : issue.message);
  results.hidden = false;
  editor.setFile(file);
  results.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (!selectedFile) {
    message("Choose a PDF or load the synthetic sample first.", true);
    picker.focus();
    return;
  }
  setBusy(true);
  message("Reading the statement and checking its totals…");
  try {
    const response = await fetch("/api/convert", {
      method: "POST", headers: { "Content-Type": "application/pdf" },
      body: selectedFile, signal: AbortSignal.timeout(70_000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "The conversion service is temporarily unavailable.");
    const bytes = Uint8Array.from(atob(data.workbook), (character) => character.charCodeAt(0));
    const file = await WorkbookFile.read(bytes);
    showResult(data, file);
    message("Review workbook ready to download.");
  } catch (error) {
    message(error.name === "TimeoutError" ? "The statement took too long to process. Try a smaller PDF." : error.message, true);
  } finally {
    setBusy(false);
  }
});

downloadButton.addEventListener("click", async () => {
  if (!editor.file) return;
  downloadButton.disabled = true;
  try {
    const binary = await editor.export();
    const url = URL.createObjectURL(new Blob([binary], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "BankLedger-Statement-Review.xlsx";
    link.click();
    editor.report("Excel download started. The balance check reflects the source PDF.");
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    editor.report(error.message || "The workbook could not be exported.", true);
  } finally {
    downloadButton.disabled = false;
  }
});
