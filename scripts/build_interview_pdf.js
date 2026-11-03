/**
 * Render docs/interview-prep.md to docs/interview-prep.pdf.
 * No native dependencies: Helvetica, WinAnsi escapes, a manual xref.
 *
 *   node scripts/build_interview_pdf.js
 */

const fs = require("fs");
const path = require("path");

const source = path.join(__dirname, "..", "docs", "interview-prep.md");
const target = path.join(__dirname, "..", "docs", "interview-prep.pdf");
const markdown = fs.readFileSync(source, "utf8").replace(/\r\n/g, "\n");

const PAGE_WIDTH = 595;
const PAGE_HEIGHT = 842;
const LEFT = 54;
const TOP = 800;
const BOTTOM = 54;

const blocks = markdownToBlocks(markdown);
const pages = paginate(blocks);

const objects = [];
const pageIds = [];
const contentIds = [];
let nextId = 3;
for (const commands of pages) {
  pageIds.push(nextId);
  contentIds.push(nextId + 1);
  nextId += 2;
}
const fontId = nextId;
const boldId = nextId + 1;

function obj(id, body) {
  return Buffer.concat([Buffer.from(`${id} 0 obj\n`), body, Buffer.from("\nendobj\n")]);
}

const kids = pageIds.map((id) => `${id} 0 R`).join(" ");
const blobs = [
  obj(1, Buffer.from("<< /Type /Catalog /Pages 2 0 R >>")),
  obj(2, Buffer.from(`<< /Type /Pages /Count ${pages.length} /Kids [${kids}] >>`)),
];
pages.forEach((commands, index) => {
  const stream = Buffer.from(commands.join("\n"), "ascii");
  blobs.push(
    obj(
      pageIds[index],
      Buffer.from(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] ` +
          `/Contents ${contentIds[index]} 0 R /Resources << /Font << /F1 ${fontId} 0 R /F2 ${boldId} 0 R >> >> >>`,
      ),
    ),
  );
  blobs.push(
    obj(
      contentIds[index],
      Buffer.concat([
        Buffer.from(`<< /Length ${stream.length} >>\nstream\n`),
        stream,
        Buffer.from("\nendstream"),
      ]),
    ),
  );
});
blobs.push(obj(fontId, Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")));
blobs.push(obj(boldId, Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")));

let pdf = Buffer.from("%PDF-1.4\n");
const offsets = blobs.map((blob) => {
  const offset = pdf.length;
  pdf = Buffer.concat([pdf, blob]);
  return offset;
});
const xrefStart = pdf.length;
const size = boldId + 1;
let xref = `xref\n0 ${size}\n0000000000 65535 f \n`;
for (const offset of offsets) {
  xref += `${String(offset).padStart(10, "0")} 00000 n \n`;
}
pdf = Buffer.concat([
  pdf,
  Buffer.from(`${xref}trailer << /Size ${size} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`),
]);
fs.writeFileSync(target, pdf);
console.log(`wrote ${target} (${pages.length} pages)`);

function markdownToBlocks(text) {
  const blocks = [];
  for (const raw of text.split(/\n\n+/)) {
    const paragraph = raw.trim();
    if (!paragraph) continue;
    if (paragraph.startsWith("### ")) {
      blocks.push({ kind: "question", text: paragraph.slice(4).replace(/\n/g, " ") });
    } else if (paragraph.startsWith("## ")) {
      blocks.push({ kind: "section", text: paragraph.slice(3).replace(/\n/g, " ") });
    } else if (paragraph.startsWith("# ")) {
      blocks.push({ kind: "title", text: paragraph.slice(2).replace(/\n/g, " ") });
    } else {
      blocks.push({ kind: "body", text: paragraph.replace(/\n/g, " ").replace(/\s+/g, " ") });
    }
  }
  return blocks;
}

function paginate(blocks) {
  const pages = [];
  let commands = [];
  let y = TOP;

  function newPage() {
    if (commands.length) pages.push(commands);
    commands = [];
    y = TOP;
  }

  for (const block of blocks) {
    const style = styleFor(block.kind);
    const lines = wrap(block.text, style.width);
    // Break between lines. A long answer must not be drawn past the bottom margin.
    if (y - style.before - style.leading < BOTTOM) newPage();
    y -= style.before;
    commands.push("BT");
    commands.push(`${style.font} ${style.size} Tf`);
    lines.forEach((line) => {
      if (y < BOTTOM) {
        commands.push("ET");
        newPage();
        commands.push("BT");
        commands.push(`${style.font} ${style.size} Tf`);
      }
      commands.push(`1 0 0 1 ${LEFT} ${y} Tm`);
      commands.push(`(${escapePdf(line)}) Tj`);
      y -= style.leading;
    });
    commands.push("ET");
  }
  if (commands.length) pages.push(commands);
  pages.forEach((page, index) => {
    page.push("BT");
    page.push("/F1 9 Tf");
    page.push(`1 0 0 1 ${LEFT} 36 Tm`);
    page.push(`(${escapePdf(`Norte Seguros claims assistant  ·  ${index + 1} / ${pages.length}`)}) Tj`);
    page.push("ET");
  });
  return pages;
}

function styleFor(kind) {
  if (kind === "title") return { font: "/F2", size: 18, leading: 22, before: 0, width: 78 };
  if (kind === "section") return { font: "/F2", size: 13, leading: 16, before: 16, width: 88 };
  if (kind === "question") return { font: "/F2", size: 11, leading: 14, before: 12, width: 92 };
  return { font: "/F1", size: 10, leading: 13, before: 6, width: 95 };
}

function wrap(text, width) {
  const words = text.split(" ");
  const lines = [];
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > width && current) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
  }
  if (current) lines.push(current);
  return lines.length ? lines : [""];
}

function escapePdf(text) {
  const raw = Buffer.from(text, "latin1");
  let out = "";
  for (const byte of raw) {
    if (byte === 0x28 || byte === 0x29 || byte === 0x5c) out += "\\" + String.fromCharCode(byte);
    else if (byte >= 32 && byte < 127) out += String.fromCharCode(byte);
    else out += `\\${byte.toString(8).padStart(3, "0")}`;
  }
  return out;
}
