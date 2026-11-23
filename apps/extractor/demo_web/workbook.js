"use strict";

// Edit the sheet XML in the workbook we generated. Other package parts, including
// styles, comments and conditional formatting, are carried through unchanged.
globalThis.WorkbookFile = (() => {
  const RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
  const XML = "http://www.w3.org/XML/1998/namespace";
  const DAY = 86400000;
  const BUILTIN_NUMBER_FORMATS = {
    1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00",
    37: "#,##0;(#,##0)", 38: "#,##0;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
  };
  const parser = new DOMParser();
  const serializer = new XMLSerializer();
  const elements = (parent, tag) => Array.from(parent?.children ?? []).filter(node => node.localName === tag);
  const first = (parent, tag) => elements(parent, tag)[0];

  function documentAt(files, path, optional = false) {
    if (!files[path]) {
      if (optional) return null;
      throw new Error("The generated workbook is missing a worksheet or its index.");
    }
    const document = parser.parseFromString(fflate.strFromU8(files[path]), "application/xml");
    if (document.getElementsByTagName("parsererror").length) throw new Error("The generated workbook could not be read.");
    return document;
  }

  function address(row, col) {
    let letters = "";
    for (let value = col + 1; value > 0; value = Math.floor((value - 1) / 26)) {
      letters = String.fromCharCode(65 + (value - 1) % 26) + letters;
    }
    return letters + (row + 1);
  }

  function position(reference) {
    const match = /^([A-Z]+)([1-9]\d*)$/.exec(reference ?? "");
    if (!match) return null;
    const col = Array.from(match[1]).reduce((value, symbol) => value * 26 + symbol.charCodeAt(0) - 64, 0);
    return [Number(match[2]) - 1, col - 1];
  }

  function color(element) {
    const value = element?.getAttribute("rgb");
    return value && /^[\dA-F]{6,8}$/i.test(value) ? "#" + value.slice(-6) : undefined;
  }

  function workbookStyles(root) {
    const custom = new Map(elements(first(root, "numFmts"), "numFmt").map(node =>
      [Number(node.getAttribute("numFmtId")), node.getAttribute("formatCode")]));
    const fonts = elements(first(root, "fonts"), "font");
    const fills = elements(first(root, "fills"), "fill");
    const borders = elements(first(root, "borders"), "border");
    return elements(first(root, "cellXfs"), "xf").map(node => {
      const id = Number(node.getAttribute("numFmtId"));
      const format = custom.get(id) || BUILTIN_NUMBER_FORMATS[id] || "";
      const font = fonts[Number(node.getAttribute("fontId"))];
      const fill = first(fills[Number(node.getAttribute("fillId"))], "patternFill");
      const border = borders[Number(node.getAttribute("borderId"))];
      const alignment = first(node, "alignment");
      const date = (id >= 14 && id <= 17) || id === 22 || /[yd]/i.test(format.replace(/"[^"]*"|\\.|\[[^\]]*\]/g, ""));
      return {
        format, id, date,
        visual: {
          bold: Boolean(first(font, "b")),
          color: color(first(font, "color")),
          fill: fill?.getAttribute("patternType") === "solid" ? color(first(fill, "fgColor")) : undefined,
          align: alignment?.getAttribute("horizontal") || undefined,
          wrap: alignment?.getAttribute("wrapText") === "1",
          topBorder: Boolean(first(border, "top")?.getAttribute("style")),
          bottomBorder: Boolean(first(border, "bottom")?.getAttribute("style")),
        },
      };
    });
  }

  const pad = value => String(value).padStart(2, "0");
  const iso = date => `${date.getUTCFullYear()}-${pad(date.getUTCMonth()+1)}-${pad(date.getUTCDate())}`;
  function dateText(date, style) {
    if (Number.isNaN(date.getTime())) return "";
    if (style?.format.toLowerCase().startsWith("yyyy")) return iso(date);
    return `${pad(date.getUTCMonth()+1)}/${pad(date.getUTCDate())}/${date.getUTCFullYear()}`;
  }

  function inputDate(value) {
    const format = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(value);
    const [year, month, day] = format ? format.slice(1).map(Number) : us ? [+us[3], +us[1], +us[2]] : [];
    if (!year || year < 1900 || year > 9999) return null;
    const parsed = new Date(Date.UTC(year, month - 1, day));
    return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day ? parsed : null;
  }

  function numberText(value, style) {
    const format = style?.format || "";
    const id = style?.id;
    const fixed = /\.(0+)/.exec(format)?.[1].length ?? ([1, 3, 37, 38].includes(id) ? 0 : id === 10 ? 2 : undefined);
    if ((fixed === undefined && !format.includes("#,##")) || !Number.isFinite(Number(value))) return value;
    const negative = Number(value) < 0;
    const digits = Math.abs(Number(value)).toLocaleString("en-US", {
      useGrouping: format.includes("#,##"),
      ...(fixed === undefined ? { maximumFractionDigits: 12 } : { minimumFractionDigits: fixed, maximumFractionDigits: fixed }),
    });
    return negative ? (format.includes("(#") ? `(${digits})` : "-" + digits) : digits;
  }

  function viewCell(cell, styles, shared, date1904) {
    const styleIndex = Number(cell.getAttribute("s") || 0);
    const style = styles[styleIndex];
    const raw = first(cell, "v")?.textContent ?? "";
    const format = cell.getAttribute("t");
    let value = raw;
    let text = raw;
    let type = "text";
    if (format === "inlineStr") {
      value = text = textRuns(first(cell, "is"));
    } else if (format === "s") {
      value = text = shared[Number(raw)] ?? "";
    } else if (format === "b") {
      value = text = raw === "1" ? "TRUE" : "FALSE";
      type = "boolean";
    } else if (format === "d" || (raw && (!format || format === "n") && style?.date)) {
      const base = date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, Number(raw) < 60 ? 31 : 30);
      value = text = dateText(format === "d" ? new Date(raw) : new Date(base + Number(raw) * DAY), style);
      type = "date";
    } else if (raw && (!format || format === "n")) {
      text = numberText(raw, style);
      type = "number";
    }
    const formula = first(cell, "f")?.textContent;
    if (formula !== undefined) return {value: "=" + formula, text: text || "=" + formula, type:"formula", formula, style:style?.visual, styleIndex};
    return {value, text, type, style:style?.visual, styleIndex};
  }

  function textRuns(container) {
    return Array.from(container?.getElementsByTagNameNS("*", "t") ?? []).map(node => node.textContent).join("");
  }

  function viewSheet(name, root, styles, shared, date1904) {
    const rows = [];
    const colWidths = [];
    const rowHeights = [];
    const dimension = first(root, "dimension")?.getAttribute("ref").split(":").at(-1);
    const [lastRow, lastCol] = position(dimension) ?? [0, 0];
    let rowCount = lastRow + 1;
    let colCount = lastCol + 1;
    for (const column of elements(first(root, "cols"), "col")) {
      const width = Math.round(Number(column.getAttribute("width")) * 7 + 5);
      for (let index = Number(column.getAttribute("min")) - 1; index < Number(column.getAttribute("max")); index++) {
        colWidths[index] = column.getAttribute("hidden") === "1" ? 0 : width;
      }
    }
    for (const row of elements(first(root, "sheetData"), "row")) {
      const index = Number(row.getAttribute("r")) - 1;
      if (row.hasAttribute("ht")) rowHeights[index] = Math.round(Number(row.getAttribute("ht")) * 4 / 3);
      rowCount = Math.max(rowCount, index + 1);
      for (const cell of elements(row, "c")) {
        const coordinates = position(cell.getAttribute("r"));
        if (!coordinates) continue;
        const [r, c] = coordinates;
        rows[r] ??= [];
        rows[r][c] = viewCell(cell, styles, shared, date1904);
        colCount = Math.max(colCount, c + 1);
      }
    }
    const pane = first(first(first(root, "sheetViews"), "sheetView"), "pane");
    return {name, rows, colWidths, rowHeights, rowCount, colCount,
      freezeRows: Number(pane?.getAttribute("ySplit") || 0), freezeCols: Number(pane?.getAttribute("xSplit") || 0)};
  }

  function editValue(value, style, originalType) {
    const original = String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, "");
    if (original.length > 32767) throw new Error("An Excel cell can contain up to 32,767 characters.");
    const trimmed = original.trim();
    if (original.startsWith("'")) return {type:"text", value:original, text:original.slice(1), stored:original.slice(1)};
    if (original.startsWith("=") && original.length > 1) {
      return {type:"formula", value:original, text:original, formula:original.slice(1), stored:original.slice(1)};
    }
    const date = inputDate(trimmed);
    if (date) return {type:"date", value:dateText(date, style), text:dateText(date, style), stored:iso(date), day:date};
    if (/^(true|false)$/i.test(trimmed)) {
      return {type:"boolean", value:trimmed.toUpperCase(), text:trimmed.toUpperCase(), stored:trimmed.toLowerCase()==="true" ? "1" : "0"};
    }
    const number = /^[+-]?(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+|[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?)$/.test(trimmed)
      ? trimmed.replaceAll(",", "") : null;
    if (number !== null && !style?.date && !(originalType === "text" && /^[0-9]+$/.test(trimmed) && trimmed.length > 15)) {
      const significantDigits = number.replace(/[^0-9]/g, "").replace(/^0+/, "").length;
      if (significantDigits <= 15 && Number.isFinite(Number(number))) {
        return {type:"number", value:number, text:numberText(number, style), stored:number};
      }
    }
    return {type:"text", value:original, text:original, stored:original};
  }

  function appendValue(root, parent, tag, value) {
    const node = root.ownerDocument.createElementNS(root.namespaceURI, tag);
    if (value !== undefined) node.textContent = value;
    parent.append(node);
    return node;
  }

  function updateSheet(root, changes, styles, date1904) {
    const sheetData = first(root, "sheetData");
    const rowNodes = new Map(elements(sheetData, "row").map(row => [Number(row.getAttribute("r")) - 1, row]));
    const oldEnd = position(first(root, "dimension")?.getAttribute("ref").split(":").at(-1)) ?? [0, 0];
    let [lastRow, lastCol] = oldEnd;
    const dateStyle = styles.findIndex(style => style.date);
    for (const [index, changesInRow] of changes) {
      let row = rowNodes.get(index);
      if (!row) {
        row = root.ownerDocument.createElementNS(root.namespaceURI, "row");
        row.setAttribute("r", String(index + 1));
        const after = elements(sheetData, "row").find(node => Number(node.getAttribute("r")) > index + 1);
        sheetData.insertBefore(row, after ?? null);
      }
      const cellNodes = new Map(elements(row, "c").map(cell => [position(cell.getAttribute("r"))?.[1], cell]));
      for (const [column, edit] of changesInRow) {
        let cell = cellNodes.get(column);
        if (!cell) {
          cell = root.ownerDocument.createElementNS(root.namespaceURI, "c");
          cell.setAttribute("r", address(index, column));
          const after = elements(row, "c").find(node => position(node.getAttribute("r"))?.[1] > column);
          row.insertBefore(cell, after ?? null);
        }
        for (const node of Array.from(cell.children)) {
          if (["v","f","is"].includes(node.localName)) node.remove();
        }
        cell.removeAttribute("t");
        if (edit.type === "formula") {
          appendValue(root, cell, "f", edit.stored);
        } else if (edit.type === "date") {
          const existing = styles[Number(cell.getAttribute("s") || 0)];
          if (dateStyle >= 0 && !existing?.date) cell.setAttribute("s", String(dateStyle));
          if (existing?.date || dateStyle >= 0) {
            const base = date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, edit.day.getTime() < Date.UTC(1900, 2, 1) ? 31 : 30);
            appendValue(root, cell, "v", String((edit.day.getTime() - base) / DAY));
          } else {
            cell.setAttribute("t","d");
            appendValue(root, cell, "v", edit.stored + "T00:00:00");
          }
        } else if (edit.type === "number" || edit.type === "boolean") {
          if (edit.type === "boolean") cell.setAttribute("t","b");
          appendValue(root, cell, "v", edit.stored);
        } else if (edit.stored !== "") {
          cell.setAttribute("t", "inlineStr");
          const inline = appendValue(root, cell, "is");
          const text = appendValue(root, inline, "t", edit.stored);
          if (edit.stored.trim() !== edit.stored) text.setAttributeNS(XML, "xml:space", "preserve");
        }
        lastRow = Math.max(lastRow, index);
        lastCol = Math.max(lastCol, column);
      }
      row.removeAttribute("spans");
    }
    first(root, "dimension")?.setAttribute("ref", "A1:" + address(lastRow, lastCol));
  }

  async function read(bytes) {
    const files = fflate.unzipSync(bytes);
    const book = documentAt(files, "xl/workbook.xml");
    const root = book.documentElement;
    const rels = documentAt(files, "xl/_rels/workbook.xml.rels").documentElement;
    const targets = new Map(elements(rels, "Relationship").map(node => [
      node.getAttribute("Id"), new URL(node.getAttribute("Target"), "https://workbook.invalid/xl/workbook.xml").pathname.slice(1),
    ]));
    const styles = workbookStyles(documentAt(files, "xl/styles.xml", true)?.documentElement);
    const shared = elements(documentAt(files, "xl/sharedStrings.xml", true)?.documentElement, "si").map(textRuns);
    const date1904 = ["1","true"].includes(first(root, "workbookPr")?.getAttribute("date1904"));
    const paths = [];
    const sheets = elements(first(root, "sheets"), "sheet").map(node => {
      const path = targets.get(node.getAttributeNS(RELATIONSHIPS,"id"));
      paths.push(path);
      return viewSheet(node.getAttribute("name"), documentAt(files,path).documentElement, styles, shared, date1904);
    });
    if (!sheets.length) throw new Error("The generated workbook contains no worksheets.");

    function formatEdit(sheet, row, col, value) {
      const before = sheets[sheet]?.rows[row]?.[col];
      const style = styles[before?.styleIndex ?? 0];
      const edit = editValue(value, style, before?.type);
      return {...edit, style:style?.visual};
    }

    async function exportFile(edits) {
      const bySheet = new Map();
      for (const {sheet,row,col,value} of edits) {
        if (!paths[sheet] || !Number.isInteger(row) || !Number.isInteger(col) || row < 0 || col < 0 ||
            row >= 1048576 || col >= 16384) throw new Error("A cell address is outside the workbook.");
        if (!bySheet.has(sheet)) bySheet.set(sheet,new Map());
        const byRow = bySheet.get(sheet);
        if (!byRow.has(row)) byRow.set(row,new Map());
        byRow.get(row).set(col,formatEdit(sheet,row,col,value));
      }
      if (!bySheet.size) return bytes;
      const output = {...files};
      for (const [sheet, changes] of bySheet) {
        const document = documentAt(files,paths[sheet]);
        updateSheet(document.documentElement,changes,styles,date1904);
        output[paths[sheet]] = fflate.strToU8(serializer.serializeToString(document));
      }
      const config = first(root,"calcPr");
      if (config) {
        config.setAttribute("fullCalcOnLoad","1");
        config.setAttribute("forceFullCalc","1");
      }
      output["xl/workbook.xml"] = fflate.strToU8(serializer.serializeToString(book));
      return fflate.zipSync(output,{level:3});
    }
    return {sheets,formatEdit,export:exportFile};
  }
  return {read};
})();
