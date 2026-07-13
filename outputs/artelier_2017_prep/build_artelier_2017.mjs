import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/camch/OneDrive/Documents/Burning Man Art Collector";
const inputPath = path.join(root, "Art-2017.xlsx");
const schemaPath = path.join(root, "burning-man-art-scraper/config/artelier_import_schema.yaml");
const outputDir = path.join(root, "outputs/artelier_2017_prep");
const outputPath = path.join(outputDir, "Burning_Man_2017_Artelier_Staging.xlsx");
const archiveRecordsPath = path.join(outputDir, "archive_2017_records.json");

const schema = JSON.parse(await fs.readFile(schemaPath, "utf8"));
const headers = schema.columns
  .slice()
  .sort((a, b) => a.order - b.order)
  .map((column) => column.name);

const input = await FileBlob.load(inputPath);
const sourceWorkbook = await SpreadsheetFile.importXlsx(input);
const sourceSheet = sourceWorkbook.worksheets.getItem("PlayaEvents-2017");
const rawValues = sourceSheet.getRange("A1:P313").values;
const sourceHeaders = rawValues[0].map((value) => normalizeHeader(value));
const rawRows = rawValues.slice(1).filter((row) => row.some((value) => String(value ?? "").trim() !== ""));
const archiveRecords = JSON.parse(await fs.readFile(archiveRecordsPath, "utf8"));
const archiveByTitle = new Map();
for (const record of archiveRecords) {
  const key = normalizeTitle(record.title);
  if (!archiveByTitle.has(key)) archiveByTitle.set(key, []);
  archiveByTitle.get(key).push(record);
}

const indexByHeader = Object.fromEntries(sourceHeaders.map((header, index) => [header, index]));
const text = (row, header) => cleanText(row[indexByHeader[header]]);
const linkText = (row) => cleanUrl(text(row, "Link"));

const normalizedRows = rawRows.map((row, offset) => {
  const title = text(row, "Title");
  const archiveMatches = archiveByTitle.get(normalizeTitle(title)) ?? [];
  const archive = archiveMatches.length === 1 ? archiveMatches[0] : null;
  const description = text(row, "Description");
  const proofUrl = linkText(row);
  const where = text(row, "Where");
  const extra = text(row, "Extra");
  const projectType = text(row, "Type") === "Art" ? "Installation" : text(row, "Type");
  const reviewFlags = [];
  if (!title) reviewFlags.push("Missing title");
  if (!description) reviewFlags.push("Missing description");
  if (!proofUrl && !archive?.source_url) reviewFlags.push("Missing valid proof URL");
  if (archiveMatches.length === 0) reviewFlags.push("No exact archive title match");
  if (archiveMatches.length > 1) reviewFlags.push("Multiple archive title matches");
  if (!archive?.image_urls?.[0]) reviewFlags.push("Missing archive hero image");
  if (/test|xxxx/i.test(title) || /xxxxx/i.test(description)) reviewFlags.push("Possible test row");

  return {
    source_year: 2017,
    source_file: "Art-2017.xlsx",
    source_sheet: "PlayaEvents-2017",
    source_row_number: offset + 2,
    project_title: title,
    project_slug: slugify(`${title} 2017`),
    project_type: projectType,
    project_year: 2017,
    artist_display_text: archive?.artist_display_text ?? "",
    artist_location: archive?.artist_location ?? "",
    playa_location: where,
    project_summary: archive?.description ?? description,
    source_extra: extra,
    legacy_proof_external_url: proofUrl,
    proof_external_url: archive?.source_url ?? proofUrl,
    hero_image_url: archive?.image_urls?.[0] ?? "",
    contributor_website: archive?.website_url ?? "",
    archive_match_status: archive ? "Exact title match" : archiveMatches.length > 1 ? "Ambiguous title match" : "No title match",
    review_status: reviewFlags.length ? "Needs Review" : "Ready for Import",
    review_notes: reviewFlags.join("; "),
  };
});

const artelierRows = normalizedRows.map((row) => {
  const artelier = Object.fromEntries(headers.map((header) => [header, ""]));
  artelier.project_title = row.project_title;
  artelier.project_slug = row.project_slug;
  artelier.project_type = row.project_type;
  artelier.project_year = row.project_year;
  artelier.project_location = row.artist_location;
  artelier.project_summary = row.project_summary;
  artelier.project_visibility = "private";
  artelier.project_context_tags = row.playa_location ? `Burning Man 2017; ${row.playa_location}` : "Burning Man 2017";
  artelier.hero_image_url = row.hero_image_url;
  artelier.contributor_name = row.artist_display_text;
  artelier.contributor_slug = slugify(row.artist_display_text);
  artelier.role_title = "Artist";
  artelier.contributor_website = row.contributor_website;
  artelier.contributor_visibility = "private";
  artelier.contribution_title = row.project_title ? `Artist contribution to ${row.project_title}` : "";
  artelier.what_they_did = row.project_summary;
  artelier.verification_status = row.proof_external_url ? "documented" : "needs_review";
  artelier.approval_status = "draft";
  artelier.contribution_visibility = "private";
  artelier.proof_title = row.project_title;
  artelier.proof_type = "Installation detail page";
  artelier.proof_external_url = row.proof_external_url;
  artelier.proof_description = row.project_summary;
  artelier.proof_visibility = "private";
  artelier.permission_status = "pending_permission";
  return artelier;
});

const reviewRows = normalizedRows
  .filter((row) => row.review_status !== "Ready for Import")
  .map((row) => [
    row.source_row_number,
    row.project_title,
    row.review_notes,
    row.archive_match_status,
    row.playa_location,
    row.proof_external_url,
    row.hero_image_url,
    row.project_summary,
  ]);

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const raw = workbook.worksheets.add("Raw_Imports");
const normalized = workbook.worksheets.add("Normalized_Projects");
const artelier = workbook.worksheets.add("Artelier_Import");
const review = workbook.worksheets.add("Review_Queue");

writeSheet(summary, [
  ["Burning Man 2017 Artelier Staging Workbook", ""],
  ["Source workbook", inputPath],
  ["Source sheet", "PlayaEvents-2017"],
  ["Total source rows", rawRows.length],
  ["Ready for import", normalizedRows.filter((row) => row.review_status === "Ready for Import").length],
  ["Needs review", reviewRows.length],
  ["Valid proof URLs", normalizedRows.filter((row) => row.proof_external_url).length],
  ["Generated workbook", outputPath],
  ["Next step", "Review missing proof URLs, enrich contributor/image/material fields, then export Artelier_Import as CSV."],
]);
summary.getRange("A1:B1").merge();

const rawMatrix = [
  ["source_year", "source_file", "source_sheet", "source_row_number", ...sourceHeaders],
  ...rawRows.map((row, index) => [2017, "Art-2017.xlsx", "PlayaEvents-2017", index + 2, ...row.map((value) => cleanText(value))]),
];
writeSheet(raw, rawMatrix);

const normalizedHeaders = [
  "source_year",
  "source_file",
  "source_sheet",
  "source_row_number",
  "project_title",
  "project_slug",
  "project_type",
  "project_year",
  "artist_display_text",
  "artist_location",
  "playa_location",
  "project_summary",
  "source_extra",
  "legacy_proof_external_url",
  "proof_external_url",
  "hero_image_url",
  "contributor_website",
  "archive_match_status",
  "review_status",
  "review_notes",
];
writeSheet(normalized, [
  normalizedHeaders,
  ...normalizedRows.map((row) => normalizedHeaders.map((header) => row[header] ?? "")),
]);

writeSheet(artelier, [
  headers,
  ...artelierRows.map((row) => headers.map((header) => row[header] ?? "")),
]);

writeSheet(review, [
  ["source_row_number", "project_title", "review_notes", "archive_match_status", "playa_location", "proof_external_url", "hero_image_url", "project_summary"],
  ...reviewRows,
]);

styleSheet(summary, "A1:B9", 2);
styleSheet(raw, `A1:T${rawMatrix.length}`, rawMatrix[0].length);
styleSheet(normalized, `A1:U${normalizedRows.length + 1}`, normalizedHeaders.length);
styleSheet(artelier, `A1:AJ${artelierRows.length + 1}`, headers.length);
styleSheet(review, `A1:H${reviewRows.length + 1}`, 8);

summary.getRange("A1:B1").format = {
  fill: "#1F2937",
  font: { bold: true, color: "#FFFFFF", size: 14 },
  horizontalAlignment: "left",
  verticalAlignment: "middle",
};
summary.getRange("A1:B1").format.rowHeightPx = 34;
summary.getRange("A2:A9").format = {
  fill: "#E5E7EB",
  font: { bold: true, color: "#111827" },
};
review.getRange(`A2:F${reviewRows.length + 1}`).format = { fill: "#FFF7ED" };

await fs.mkdir(outputDir, { recursive: true });

for (const [sheetName, range] of [
  ["Summary", "A1:B9"],
  ["Normalized_Projects", "A1:K20"],
  ["Artelier_Import", "A1:Q20"],
  ["Review_Queue", "A1:H20"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(
    path.join(outputDir, `${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errorScan.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({
  outputPath,
  sourceRows: rawRows.length,
  readyForImport: normalizedRows.filter((row) => row.review_status === "Ready for Import").length,
  needsReview: reviewRows.length,
  archiveRecords: archiveRecords.length,
  archiveMatchedRows: normalizedRows.filter((row) => row.archive_match_status === "Exact title match").length,
  heroImageRows: normalizedRows.filter((row) => row.hero_image_url).length,
  artelierColumns: headers.length,
}, null, 2));

function writeSheet(sheet, matrix) {
  const rowCount = matrix.length;
  const colCount = matrix[0]?.length ?? 1;
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).values = matrix;
}

function styleSheet(sheet, range, colCount) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const full = sheet.getRange(range);
  full.format = {
    font: { name: "Aptos", size: 10, color: "#111827" },
    wrapText: true,
    verticalAlignment: "top",
  };
  const header = sheet.getRangeByIndexes(0, 0, 1, colCount);
  header.format = {
    fill: "#0F766E",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "middle",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#0F766E" },
  };
  full.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    insideVertical: { style: "thin", color: "#F3F4F6" },
    bottom: { style: "thin", color: "#D1D5DB" },
  };
  full.format.autofitColumns();
  full.format.autofitRows();
}

function normalizeHeader(value) {
  if (value instanceof Date) {
    return value.toISOString().slice(0, 10);
  }
  const stringValue = String(value ?? "").trim();
  const dateMatch = stringValue.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return dateMatch ? dateMatch[0] : stringValue;
}

function cleanText(value) {
  return String(value ?? "")
    .replace(/_x000D_/g, "\n")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function cleanUrl(value) {
  const url = cleanText(value);
  return /^https?:\/\//i.test(url) ? url : "";
}

function slugify(value) {
  return cleanText(value)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 90);
}

function normalizeTitle(value) {
  return cleanText(value)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}
