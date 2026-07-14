export const ARTELIER_SCRAPER_CSV_V1 = "artelier_scraper_csv_v1" as const;
export const LEGACY_SCRAPER_CSV = "legacy_scraper" as const;

export const CONTRIBUTOR_KINDS = [
  "person",
  "organization",
  "collective",
  "unknown",
] as const;

export const SOURCE_GRANULARITY_VALUES = [
  "Individual project page",
  "Portfolio index page",
  "Gallery caption",
  "Bio/CV reference",
  "Press article",
  "Image-only inference",
] as const;

export const CONFIDENCE_VALUES = ["high", "medium", "low"] as const;
export const CLASSIFICATION_SOURCE_VALUES = [
  "manual",
  "scraper",
  "ai",
  "imported",
] as const;
export const REVIEW_STATUS_VALUES = [
  "Needs review",
  "Approved",
  "Rejected",
  "Needs better source",
  "Needs permission",
  "Duplicate",
] as const;
export const PERMISSION_STATUS_VALUES = [
  "Needs permission",
  "Permission granted",
  "Permission not required",
  "Do not publish",
  "Unknown",
] as const;

export const ARTELIER_SCRAPER_CSV_V1_COLUMNS = [
  "contract_version",
  "source_name",
  "source_namespace",
  "source_record_id",
  "source_record_url",
  "contributor_kind",
  "artist_name",
  "artist_alias",
  "artist_website",
  "organization_name",
  "project_title",
  "proof_title",
  "proof_external_url",
  "proof_excerpt",
  "source_granularity",
  "project_type",
  "tags",
  "materials",
  "fabrication_methods",
  "context_tags",
  "what_they_did",
  "why_it_matters",
  "contributor_role",
  "collaboration_status",
  "collaborators",
  "location",
  "year",
  "dimensions",
  "client_or_commissioner",
  "institution",
  "image_urls",
  "proof_confidence",
  "classification_confidence",
  "description_confidence",
  "classification_source",
  "review_status",
  "permission_status",
  "import_notes",
] as const;

export type ArtelierScraperCsvV1Column =
  (typeof ARTELIER_SCRAPER_CSV_V1_COLUMNS)[number];
export type ContributorKind = (typeof CONTRIBUTOR_KINDS)[number];
export type ArtelierScraperCsvRow = Partial<
  Record<ArtelierScraperCsvV1Column, string>
>;

export const ARTELIER_SCRAPER_CSV_V1_ALIAS_MAP: Record<
  string,
  ArtelierScraperCsvV1Column
> = {
  contributor_name: "artist_name",
  creator_name: "artist_name",
  contributor_website: "artist_website",
  website: "artist_website",
  role_title: "contributor_role",
  contributor_role_title: "contributor_role",
  title: "project_title",
  type: "project_type",
  proof_url: "proof_external_url",
  source_url: "proof_external_url",
  source_excerpt: "proof_excerpt",
  why_it_mattered: "why_it_matters",
  client_name: "client_or_commissioner",
  commissioner: "client_or_commissioner",
  client: "client_or_commissioner",
  image_url: "image_urls",
  hero_image_url: "image_urls",
  image_content_link: "image_urls",
};

export type ContractKind =
  | typeof ARTELIER_SCRAPER_CSV_V1
  | typeof LEGACY_SCRAPER_CSV
  | "unsupported"
  | "mixed";

export type ContractDetection = {
  kind: ContractKind;
  declaredVersions: string[];
  errors: string[];
  warnings: string[];
};

export type ContractIdentity = {
  contributorKind: ContributorKind;
  canonicalName: string;
  alternateName: string;
  organizationName: string;
};

export type ContractRowValidation = {
  errors: string[];
  warnings: string[];
  identity: ContractIdentity;
};

export type IdentityPromotionRoute = "person" | "organization" | "blocked";

const hasOwn = (row: Record<string, string>, key: string) =>
  Object.prototype.hasOwnProperty.call(row, key);

export function detectScraperCsvContract(
  headers: string[],
  rows: Record<string, string>[],
): ContractDetection {
  if (!headers.map((header) => header.trim()).includes("contract_version")) {
    return {
      kind: LEGACY_SCRAPER_CSV,
      declaredVersions: [],
      errors: [],
      warnings: [
        "No contract_version header detected; treating this file as a legacy scraper import.",
      ],
    };
  }

  const values = rows.map((row) => (row.contract_version ?? "").trim());
  const declaredVersions = Array.from(
    new Set(values.filter(Boolean)),
  ).sort();
  const blankRows = values.filter((value) => !value).length;

  if (declaredVersions.length === 1 && declaredVersions[0] === ARTELIER_SCRAPER_CSV_V1) {
    const errors =
      blankRows > 0
        ? [`${blankRows} row(s) are missing contract_version.`]
        : [];
    return {
      kind: ARTELIER_SCRAPER_CSV_V1,
      declaredVersions,
      errors,
      warnings: [],
    };
  }

  if (declaredVersions.length > 1) {
    return {
      kind: "mixed",
      declaredVersions,
      errors: [
        `Mixed contract versions are not supported: ${declaredVersions.join(", ")}.`,
      ],
      warnings: [],
    };
  }

  const value = declaredVersions[0] ?? "(blank)";
  return {
    kind: "unsupported",
    declaredVersions,
    errors: [`Unsupported contract_version "${value}".`],
    warnings: [],
  };
}

export function normalizeContributorKind(
  value: string | null | undefined,
): ContributorKind {
  const normalized = (value ?? "").trim().toLowerCase();
  return (CONTRIBUTOR_KINDS as readonly string[]).includes(normalized)
    ? (normalized as ContributorKind)
    : "unknown";
}

export function contractIdentity(row: ArtelierScraperCsvRow): ContractIdentity {
  const contributorKind = normalizeContributorKind(row.contributor_kind);
  const artistName = (row.artist_name ?? "").trim();
  const artistAlias = (row.artist_alias ?? "").trim();
  const organizationName = (row.organization_name ?? "").trim();
  return {
    contributorKind,
    canonicalName:
      contributorKind === "organization"
        ? organizationName || artistName
        : artistName || organizationName,
    alternateName: artistAlias,
    organizationName,
  };
}

export function identityPromotionRoute(
  contract: ContractKind,
  contributorKind: ContributorKind,
): IdentityPromotionRoute {
  if (contract === LEGACY_SCRAPER_CSV) return "person";
  if (contract !== ARTELIER_SCRAPER_CSV_V1) return "blocked";
  if (contributorKind === "person") return "person";
  if (contributorKind === "organization") return "organization";
  return "blocked";
}

function validHttpUrl(value: string): boolean {
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}

export function validateScraperContractRow(
  row: ArtelierScraperCsvRow,
  contract: ContractKind,
): ContractRowValidation {
  const errors: string[] = [];
  const warnings: string[] = [];
  const identity = contractIdentity(row);

  if (
    contract === ARTELIER_SCRAPER_CSV_V1 &&
    row.contract_version !== ARTELIER_SCRAPER_CSV_V1
  ) {
    errors.push(`contract_version must be "${ARTELIER_SCRAPER_CSV_V1}".`);
  }

  if (
    row.contributor_kind &&
    !(CONTRIBUTOR_KINDS as readonly string[]).includes(
      row.contributor_kind.trim().toLowerCase(),
    )
  ) {
    warnings.push(
      `Unknown contributor_kind "${row.contributor_kind}"; treating it as unknown.`,
    );
  } else if (
    contract === ARTELIER_SCRAPER_CSV_V1 &&
    !row.contributor_kind
  ) {
    warnings.push("contributor_kind is missing; treating it as unknown.");
  }

  if (identity.contributorKind === "organization" && !identity.organizationName) {
    warnings.push("organization_name is recommended when contributor_kind is organization.");
  }
  if (
    identity.contributorKind === "person" &&
    !(row.artist_name ?? "").trim()
  ) {
    warnings.push("artist_name is recommended when contributor_kind is person.");
  }
  if (
    (identity.contributorKind === "collective" ||
      identity.contributorKind === "unknown") &&
    contract === ARTELIER_SCRAPER_CSV_V1
  ) {
    warnings.push(
      `${identity.contributorKind} identities remain in staging until a reviewer resolves contributor_kind.`,
    );
  }

  for (const field of [
    "artist_website",
    "proof_external_url",
    "source_record_url",
  ] as const) {
    const value = (row[field] ?? "").trim();
    if (value && !validHttpUrl(value)) {
      warnings.push(`${field} is not a valid HTTP(S) URL.`);
    }
  }

  const controlled: Array<{
    field: ArtelierScraperCsvV1Column;
    values: readonly string[];
  }> = [
    { field: "source_granularity", values: SOURCE_GRANULARITY_VALUES },
    { field: "proof_confidence", values: CONFIDENCE_VALUES },
    { field: "classification_confidence", values: CONFIDENCE_VALUES },
    { field: "description_confidence", values: CONFIDENCE_VALUES },
    { field: "classification_source", values: CLASSIFICATION_SOURCE_VALUES },
    { field: "review_status", values: REVIEW_STATUS_VALUES },
    { field: "permission_status", values: PERMISSION_STATUS_VALUES },
  ];
  for (const { field, values } of controlled) {
    const value = (row[field] ?? "").trim();
    if (value && !values.includes(value)) {
      warnings.push(`Unknown ${field} "${value}".`);
    }
  }

  if (
    contract === ARTELIER_SCRAPER_CSV_V1 &&
    hasOwn(row as Record<string, string>, "source_namespace") &&
    !(row.source_namespace ?? "").trim()
  ) {
    warnings.push("source_namespace is blank.");
  }

  return { errors, warnings, identity };
}
