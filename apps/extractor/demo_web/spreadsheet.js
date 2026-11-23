"use strict";

(() => {
  const CELL_LIMIT = 32767;
  const REGISTER_LABEL_WIDTHS = {1: 128, 10: 37};
  const key = (row, col) => `${row}:${col}`;

  function letters(index) {
    let label = "";
    for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) {
      label = String.fromCharCode(65 + (value - 1) % 26) + label;
    }
    return label;
  }

  function add(parent, tag, text = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  }

  function clipboardRows(text) {
    const rows = [[]];
    let field = "";
    let quoted = false;
    for (let index = 0; index < text.length; index++) {
      const char = text[index];
      if (char === '"' && quoted && text[index + 1] === '"') {
        field += '"';
        index++;
      } else if (char === '"' && (quoted || !field)) {
        quoted = !quoted;
      } else if (!quoted && (char === "\t" || char === "\r" || char === "\n")) {
        rows.at(-1).push(field);
        field = "";
        if (char !== "\t") {
          if (char === "\r" && text[index + 1] === "\n") index++;
          if (index + 1 < text.length) rows.push([]);
        }
      } else {
        field += char;
      }
    }
    if (!text || field || text.endsWith("\t") || !/[\r\n]$/.test(text)) rows.at(-1).push(field);
    return rows;
  }

  function clipboardField(value) {
    return /[\t\n\r"]/.test(value) ? '"' + value.replaceAll('"', '""') + '"' : value;
  }

  class WorkbookGrid {
    constructor(root) {
      const element = (id) => root.querySelector("#" + id);
      this.table = element("sheet-grid");
      this.viewport = this.table.parentElement;
      this.panel = element("sheet-panel");
      this.tabs = element("sheet-tabs");
      this.address = element("sheet-address");
      this.formula = element("sheet-value");
      this.changeStatus = element("sheet-changes");
      this.status = element("sheet-export-status");
      this.undoButton = element("sheet-undo");
      this.redoButton = element("sheet-redo");
      this.formula.maxLength = CELL_LIMIT;

      this.table.addEventListener("pointerdown", (event) => this.pointerDown(event));
      this.table.addEventListener("pointerover", (event) => this.pointerOver(event));
      window.addEventListener("pointerup", () => { this.drag = null; });
      window.addEventListener("pointercancel", () => { this.drag = null; });
      this.table.addEventListener("dblclick", (event) => {
        if (event.target.closest("td")) this.startEditor();
      });
      this.table.addEventListener("keydown", (event) => this.keydown(event));
      this.table.addEventListener("copy", (event) => this.copy(event, false));
      this.table.addEventListener("cut", (event) => this.copy(event, true));
      this.table.addEventListener("paste", (event) => this.paste(event));
      this.tabs.addEventListener("keydown", (event) => this.tabKeydown(event));
      this.undoButton.addEventListener("click", () => this.undo());
      this.redoButton.addEventListener("click", () => this.redo());
      this.formula.addEventListener("focus", () => {
        if (this.file) this.formulaCell = { ...this.active };
      });
      this.formula.addEventListener("blur", () => this.commitFormula());
      this.formula.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          this.formulaCell = null;
          this.syncFormula();
          this.table.focus({preventScroll: true});
        } else if (event.key === "Enter" && !event.altKey && !event.shiftKey) {
          event.preventDefault();
          this.commitFormula();
          this.table.focus({preventScroll: true});
        }
      });
    }

    setFile(file) {
      this.file = file;
      this.activeSheet = -1;
      this.edits = new Map();
      this.history = [];
      this.future = [];
      this.views = new Map();
      this.editing = null;
      this.formulaCell = null;
      this.tabButtons = file.sheets.map((sheet, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.id = `sheet-tab-${index}`;
        button.textContent = sheet.name;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-controls", "sheet-panel");
        button.addEventListener("click", () => this.activate(index));
        return button;
      });
      this.tabs.replaceChildren(...this.tabButtons);
      this.activate(file.activeSheet ?? 0);
      this.updateChanges();
      this.report("");
    }

    get sheet() {
      return this.file.sheets[this.activeSheet];
    }

    get rowCount() {
      return Math.max(this.sheet.rowCount, 1);
    }

    get colCount() {
      return Math.max(this.sheet.colCount, 1);
    }

    original(sheet, row, col) {
      return this.file.sheets[sheet].rows[row]?.[col];
    }

    cell(sheet, row, col) {
      return this.edits.get(`${sheet}:${key(row, col)}`)?.formatted ?? this.original(sheet, row, col);
    }

    dom(row, col) {
      return this.table.tBodies[0].rows[row].cells[col + 1];
    }

    clamp(row, col) {
      return { row: Math.min(Math.max(row, 0), this.rowCount - 1), col: Math.min(Math.max(col, 0), this.colCount - 1) };
    }

    activate(index) {
      if (this.activeSheet === index) return;
      this.commitEditor();
      this.commitFormula();
      if (this.activeSheet !== -1) {
        this.views.set(this.activeSheet, {
          active: this.active, anchor: this.anchor, selectionKind: this.selectionKind,
          left: this.viewport.scrollLeft, top: this.viewport.scrollTop,
        });
      }
      this.activeSheet = index;
      const view = this.views.get(index);
      this.active = view?.active ?? {row: 0, col: 0};
      this.anchor = view?.anchor ?? this.active;
      this.selectionKind = view?.selectionKind ?? "cell";
      for (const [tabIndex, tab] of this.tabButtons.entries()) {
        tab.tabIndex = index === tabIndex ? 0 : -1;
        tab.setAttribute("aria-selected", String(index === tabIndex));
      }
      const tab = this.tabButtons[index];
      this.panel.setAttribute("aria-labelledby", tab.id);
      this.table.setAttribute("aria-labelledby", tab.id);
      this.table.setAttribute("aria-rowcount", String(this.rowCount + 1));
      this.table.setAttribute("aria-colcount", String(this.colCount + 1));
      this.render();
      this.viewport.scrollLeft = view?.left ?? 0;
      this.viewport.scrollTop = view?.top ?? 0;
      this.paintSelection();
    }

    render() {
      const fragment = document.createDocumentFragment();
      const columns = add(fragment, "colgroup");
      let width = 42;
      const frozen = [];
      this.frozenWidth = 0;
      add(columns, "col").style.width = "42px";
      for (let col = 0; col < this.colCount; col++) {
        const labelWidth = this.sheet.name === "Statement Register" ? REGISTER_LABEL_WIDTHS[col] ?? 0 : 0;
        const size = Math.max(9, labelWidth, Math.min(this.sheet.colWidths[col] ?? 100, 540));
        add(columns, "col").style.width = `${size}px`;
        if (col < (this.sheet.freezeCols ?? 0)) {
          frozen[col] = width;
          this.frozenWidth += size;
        }
        width += size;
      }
      const stickColumn = (cell, col) => {
        if (frozen[col] !== undefined) {
          cell.classList.add("sheet-frozen-col");
          cell.style.left = `${frozen[col]}px`;
        }
      };
      this.table.style.width = `${width}px`;
      const header = add(add(fragment, "thead"), "tr");
      header.setAttribute("role", "row");
      const corner = add(header, "th", "◩");
      corner.dataset.kind = "all";
      corner.setAttribute("aria-label", "Select all cells");
      corner.title = "Select all cells";
      this.colHeaders = [];
      for (let col = 0; col < this.colCount; col++) {
        const column = letters(col);
        const th = add(header, "th", column);
        th.dataset.kind = "column";
        th.dataset.col = col;
        th.scope = "col";
        th.title = `Select column ${column}`;
        stickColumn(th, col);
        this.colHeaders.push(th);
      }

      const body = add(fragment, "tbody");
      this.rowHeaders = [];
      for (let row = 0; row < this.rowCount; row++) {
        const tr = add(body, "tr");
        tr.setAttribute("role", "row");
        const height = this.sheet.rowHeights?.[row];
        if (height) tr.style.height = `${height}px`;
        if (row === 0 && this.sheet.freezeRows) tr.className = "sheet-frozen";
        const label = add(tr, "th", row + 1);
        label.scope = "row";
        label.dataset.kind = "row";
        label.dataset.row = row;
        label.title = `Select row ${row + 1}`;
        this.rowHeaders.push(label);
        for (let col = 0; col < this.colCount; col++) {
          const td = add(tr, "td");
          td.id = `cell-${letters(col)}${row + 1}`;
          td.setAttribute("role", "gridcell");
          td.dataset.kind = "cell";
          td.dataset.row = row;
          td.dataset.col = col;
          stickColumn(td, col);
          this.renderCell(td, row, col);
        }
      }
      this.table.replaceChildren(fragment);
      this.marked = [];
      this.markedHeaders = [];
    }

    renderCell(td, row, col) {
      const active = this.activeSheet;
      const value = this.cell(active, row, col);
      const appearance = this.original(active, row, col)?.style ?? {};
      td.textContent = value?.text ?? "";
      td.title = value?.text ?? "";
      td.dataset.type = value?.type ?? "";
      td.dataset.align = appearance.align ?? "";
      for (const option of ["bold", "wrap", "topBorder", "bottomBorder"]) {
        td.classList.toggle(`style-${option}`, Boolean(appearance[option]));
      }
      if (appearance.color) td.style.color = appearance.color;
      if (appearance.fill) td.style.backgroundColor = appearance.fill;
    }

    range() {
      const { row, col } = this.active;
      return {
        top: ["column", "all"].includes(this.selectionKind) ? 0 : Math.min(row, this.anchor.row),
        bottom: ["column", "all"].includes(this.selectionKind) ? this.rowCount - 1 : Math.max(row, this.anchor.row),
        left: ["row", "all"].includes(this.selectionKind) ? 0 : Math.min(col, this.anchor.col),
        right: ["row", "all"].includes(this.selectionKind) ? this.colCount - 1 : Math.max(col, this.anchor.col),
      };
    }

    select(row, col, extend = false, kind = "cell") {
      this.commitEditor();
      this.commitFormula();
      this.active = this.clamp(row, col);
      if (!extend || this.selectionKind !== kind) this.anchor = this.active;
      this.selectionKind = kind;
      this.paintSelection();
      this.table.focus({preventScroll: true});
    }

    paintSelection() {
      for (const td of this.marked) {
        td.removeAttribute("aria-selected");
        td.classList.remove("sheet-active");
      }
      for (const th of this.markedHeaders) th.classList.remove("sheet-heading-selected");
      const {top, bottom, left, right} = this.range();
      this.marked = [];
      this.markedHeaders = [];
      for (let row = top; row <= bottom; row++) {
        this.markedHeaders.push(this.rowHeaders[row]);
        for (let col = left; col <= right; col++) this.marked.push(this.dom(row, col));
      }
      this.markedHeaders.push(...this.colHeaders.slice(left, right + 1));
      for (const th of this.markedHeaders) th.classList.add("sheet-heading-selected");
      for (const td of this.marked) td.setAttribute("aria-selected", "true");
      const active = this.dom(this.active.row, this.active.col);
      active.classList.add("sheet-active");
      this.table.setAttribute("aria-activedescendant", active.id);
      const first = `${letters(left)}${top + 1}`;
      const last = `${letters(right)}${bottom + 1}`;
      this.address.textContent = first === last ? first : `${first}:${last}`;
      this.syncFormula();
    }

    syncFormula() {
      if (this.file && !this.formulaCell) {
        this.formula.value = this.cell(this.activeSheet, this.active.row, this.active.col)?.value ?? "";
      }
    }

    pointerDown(event) {
      const item = event.target.closest("[data-kind]");
      if (event.button !== 0 || !item || event.target.closest("textarea")) return;
      const {kind, row, col} = item.dataset;
      const rowIndex = Number(row ?? 0);
      const colIndex = Number(col ?? 0);
      if (event.pointerType !== "touch") event.preventDefault();
      this.select(rowIndex, colIndex, event.shiftKey, kind);
      if (kind !== "all" && event.pointerType !== "touch") this.drag = kind;
    }

    pointerOver(event) {
      const item = event.target.closest("[data-kind]");
      if (!item || !this.drag || item.dataset.kind !== this.drag) return;
      if (!event.buttons) {
        this.drag = null;
        return;
      }
      const {row, col} = item.dataset;
      const next = this.clamp(Number(row ?? 0), Number(col ?? 0));
      if (next.row === this.active.row && next.col === this.active.col) return;
      this.select(next.row, next.col, true, this.drag);
    }

    move(row, col, extend = false) {
      this.select(row, col, extend);
      const target = this.dom(this.active.row, this.active.col);
      const bounds = target.getBoundingClientRect();
      const viewport = this.viewport.getBoundingClientRect();
      const firstRow = this.sheet.freezeRows ? this.table.tBodies[0].rows[0].getBoundingClientRect().height : 0;
      const top = viewport.top + this.table.tHead.getBoundingClientRect().height + firstRow;
      const left = viewport.left + this.rowHeaders[0].getBoundingClientRect().width + this.frozenWidth;
      this.viewport.scrollBy({
        top: bounds.top < top && !(this.sheet.freezeRows && this.active.row === 0) ? bounds.top - top :
          Math.max(0, bounds.bottom - viewport.bottom),
        left: this.active.col < (this.sheet.freezeCols ?? 0) ? 0 :
          bounds.left < left ? bounds.left - left : Math.max(0, bounds.right - viewport.right),
      });
    }

    keydown(event) {
      if (this.editing || event.target !== this.table) return;
      const modifier = event.ctrlKey || event.metaKey;
      const {row, col} = this.active;
      let deltaRow = 0;
      let deltaCol = 0;
      switch (event.key) {
        case "ArrowDown": deltaRow = modifier ? this.rowCount : 1; break;
        case "ArrowUp": deltaRow = modifier ? -this.rowCount : -1; break;
        case "ArrowRight": deltaCol = modifier ? this.colCount : 1; break;
        case "ArrowLeft": deltaCol = modifier ? -this.colCount : -1; break;
        case "PageDown": deltaRow = Math.floor(this.viewport.clientHeight / 29); break;
        case "PageUp": deltaRow = -Math.floor(this.viewport.clientHeight / 29); break;
        case "Home": deltaCol = -col; deltaRow = modifier ? -row : 0; break;
        case "End": deltaCol = this.colCount - col - 1; deltaRow = modifier ? this.rowCount - row - 1 : 0; break;
        case "Enter": deltaRow = event.shiftKey ? -1 : 1; break;
        case "Tab":
          if (event.shiftKey ? col === 0 : col === this.colCount - 1) return;
          deltaCol = event.shiftKey ? -1 : 1;
          break;
        case "Delete":
        case "Backspace":
          event.preventDefault();
          this.clearRange();
          return;
        case "F2":
          event.preventDefault();
          this.startEditor();
          return;
        default:
          return this.keyCommand(event, modifier);
      }
      event.preventDefault();
      this.move(row + deltaRow, col + deltaCol, event.shiftKey && !["Enter", "Tab"].includes(event.key));
    }

    keyCommand(event, modifier) {
      const pressed = event.key.toLowerCase();
      if (modifier && ["a", "z", "y"].includes(pressed)) {
        event.preventDefault();
        if (pressed === "a") this.select(0, 0, false, "all");
        else if (pressed === "y" || event.shiftKey) this.redo();
        else this.undo();
      } else if ((modifier || event.shiftKey) && event.key === " ") {
        event.preventDefault();
        this.select(this.active.row, this.active.col, false, modifier ? "column" : "row");
      } else if (!modifier && !event.altKey && !event.isComposing && event.key.length === 1) {
        event.preventDefault();
        this.startEditor(event.key);
      }
    }

    tabKeydown(event) {
      if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const count = this.tabButtons.length;
      const index = event.key === "Home" ? 0 : event.key === "End" ? count - 1 :
        (this.activeSheet + (event.key === "ArrowRight" ? 1 : -1) + count) % count;
      this.activate(index);
      const tab = this.tabButtons[index];
      tab.focus({preventScroll: true});
      const strip = this.tabs.getBoundingClientRect();
      const selected = tab.getBoundingClientRect();
      if (selected.left < strip.left) this.tabs.scrollLeft -= strip.left - selected.left;
      else if (selected.right > strip.right) this.tabs.scrollLeft += selected.right - strip.right;
    }

    startEditor(initial) {
      if (this.editing) return;
      const {row, col} = this.active;
      const td = this.dom(row, col);
      const input = add(td, "textarea");
      input.className = "inline-cell-editor";
      input.rows = 1;
      input.maxLength = CELL_LIMIT;
      input.setAttribute("aria-label", `Edit cell ${letters(col)}${row + 1}`);
      input.value = initial ?? this.cell(this.activeSheet, row, col)?.value ?? "";
      td.classList.add("sheet-editing");
      this.editing = {row, col, input, td};
      input.addEventListener("blur", () => this.commitEditor());
      input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          this.commitEditor(false);
          this.table.focus({preventScroll: true});
        } else if (event.key === "Tab" || (event.key === "Enter" && !event.altKey)) {
          event.preventDefault();
          const step = event.shiftKey ? -1 : 1;
          this.commitEditor();
          this.move(row + (event.key === "Enter" ? step : 0), col + (event.key === "Tab" ? step : 0));
        }
      });
      input.focus({preventScroll: true});
      input.setSelectionRange(input.value.length, input.value.length);
    }

    commitEditor(save = true) {
      if (!this.editing) return;
      const {row, col, input, td} = this.editing;
      this.editing = null;
      td.classList.remove("sheet-editing");
      if (save) this.change([{row, col, value: input.value}]);
      this.renderCell(td, row, col);
    }

    commitFormula() {
      if (!this.formulaCell) return;
      const position = this.formulaCell;
      this.formulaCell = null;
      this.change([{...position, value: this.formula.value}]);
      this.syncFormula();
    }

    change(edits) {
      const sheet = this.activeSheet;
      const changes = [];
      try {
        for (const {row, col, value} of edits) {
          const id = `${sheet}:${key(row, col)}`;
          const previous = this.edits.get(id);
          const original = this.original(sheet, row, col)?.value ?? "";
          if (value === (previous?.value ?? original)) continue;
          if (value.length > CELL_LIMIT) throw new Error("A cell can contain up to 32,767 characters.");
          const next = value === original ? undefined : {
            sheet, row, col, value, formatted: this.file.formatEdit(sheet, row, col, value),
          };
          changes.push({id, sheet, row, col, previous, next});
        }
      } catch (error) {
        this.report(error.message, true);
        return;
      }
      if (!changes.length) return;
      this.history.push(changes);
      this.future = [];
      this.apply(changes, "next");
    }

    apply(changes, side) {
      for (const change of changes) {
        if (change[side]) this.edits.set(change.id, change[side]);
        else this.edits.delete(change.id);
        if (change.sheet === this.activeSheet) this.renderCell(this.dom(change.row, change.col), change.row, change.col);
      }
      this.syncFormula();
      this.updateChanges();
    }

    updateChanges() {
      this.undoButton.disabled = !this.history.length;
      this.redoButton.disabled = !this.future.length;
      this.changeStatus.hidden = !this.edits.size;
      this.changeStatus.textContent = `${this.edits.size} edited ${this.edits.size === 1 ? "cell" : "cells"}`;
      this.report(this.edits.size ? "Download Excel to save your edits. The balance check reflects the source PDF." : "");
    }

    undo() {
      const changes = this.history.pop();
      if (!changes) return;
      this.future.push(changes);
      this.apply(changes, "previous");
    }

    redo() {
      const changes = this.future.pop();
      if (!changes) return;
      this.history.push(changes);
      this.apply(changes, "next");
    }

    clearRange() {
      const {top, bottom, left, right} = this.range();
      const changes = [];
      for (let row = top; row <= bottom; row++) {
        for (let col = left; col <= right; col++) changes.push({row, col, value: ""});
      }
      this.change(changes);
    }

    copy(event, cut) {
      if (this.editing || !event.clipboardData) return;
      const {top, bottom, left, right} = this.range();
      const lines = [];
      for (let row = top; row <= bottom; row++) {
        const fields = [];
        for (let col = left; col <= right; col++) fields.push(clipboardField(this.cell(this.activeSheet, row, col)?.value ?? ""));
        lines.push(fields.join("\t"));
      }
      event.preventDefault();
      event.clipboardData.setData("text/plain", lines.join("\r\n"));
      if (cut) this.clearRange();
    }

    paste(event) {
      if (this.editing || !event.clipboardData) return;
      event.preventDefault();
      const clipboard = clipboardRows(event.clipboardData.getData("text/plain"));
      const {top, left} = this.range();
      const cols = Math.max(...clipboard.map((row) => row.length));
      if (top + clipboard.length > this.rowCount || left + cols > this.colCount) {
        this.report("The pasted range extends past the available rows or columns.", true);
        return;
      }
      this.change(clipboard.flatMap((row, index) => row.map((value, col) => ({row: top + index, col: left + col, value}))));
      this.select(top, left);
      this.select(top + clipboard.length - 1, left + cols - 1, true);
    }

    report(message, error = false) {
      this.status.textContent = message;
      this.status.hidden = !message;
      this.status.classList.toggle("error", error);
    }

    async export() {
      this.commitEditor();
      this.commitFormula();
      return this.file.export(Array.from(this.edits.values(), ({sheet, row, col, value}) => ({sheet, row, col, value})));
    }
  }

  window.WorkbookGrid = WorkbookGrid;
})();
