export interface KeywordCategoryMap {
  [category: string]: string[];
}

export const DEFAULT_MAX_NOTIFICATIONS_PER_RUN = 10;

export const KEYWORD_CATEGORIES: KeywordCategoryMap = {
  Recruitment: [
    "new vacancy",
    "recruitment",
    "notification",
    "advertisement",
    "apply online",
    "application form",
    "posts",
  ],
  Examination: [
    "exam date",
    "examination date",
    "exam schedule",
    "admit card",
    "hall ticket",
    "city intimation",
  ],
  Results: [
    "result",
    "scorecard",
    "merit list",
    "cut off",
    "cutoff",
    "selection list",
  ],
  "Answer Keys": [
    "answer key",
    "answerkey",
    "provisional answer key",
    "final answer key",
    "response sheet",
  ],
  "Next Stages": [
    "DV",
    "document verification",
    "skill test",
    "typing test",
    "interview",
    "PET",
    "PST",
    "medical examination",
  ],
};

const NON_RECRUITMENT_NOISE_PHRASES = [
  "money market operations",
  "variable rate repo",
  "variable rate reverse repo",
  "liquidity adjustment facility",
  "standing deposit facility",
  "marginal standing facility",
  "treasury bills auction",
  "state development loans",
  "auction of government of india",
  "government securities",
  "underwriting auction",
  "auction result",
  "sovereign gold bond",
  "financial result",
  "financial results",
  "unaudited financial",
  "audited financial",
  "working results",
  "fema notification",
  "foreign exchange management",
  "master direction",
  "monetary policy",
  "sarfaesi",
  "e-auction",
  "sale notice",
  "possession notice",
  "property for sale",
  "premises required",
  "premises on lease",
  "technical bid opening",
  "rfp for selection",
  "rfp for procurement",
  "supply, installation",
  "private car package policy",
  "international year of cooperatives",
  "functions of departments / divisions",
  "history in brief",
  "apply online for home loan",
  "apply online for car loan",
  "apply online for personal loan",
  "apply online for gold loan",
  "apply online for education loan",
  "apply online for mudra loan",
  "apply online for msme loan",
  "apply online for credit card",
  "apply online for locker",
  "notice inviting tender",
  "corrigendum to tender",
  "tender notification",
  "e-tender for",
  "leasing-in of",
  "visit uco bank on whatsapp",
];

const COMPOUND_SPLITS: Record<string, [string, string]> = {
  scorecard: ["score", "card"],
  scorecards: ["score", "card"],
  cutoff: ["cut", "off"],
  cutoffs: ["cut", "off"],
  answerkey: ["answer", "key"],
  answerkeys: ["answer", "key"],
  admitcard: ["admit", "card"],
  admitcards: ["admit", "card"],
  hallticket: ["hall", "ticket"],
  halltickets: ["hall", "ticket"],
  responsesheet: ["response", "sheet"],
  responsesheets: ["response", "sheet"],
  meritlist: ["merit", "list"],
  meritlists: ["merit", "list"],
  selectionlist: ["selection", "list"],
  selectionlists: ["selection", "list"],
  skilltest: ["skill", "test"],
  skilltests: ["skill", "test"],
  typingtest: ["typing", "test"],
  typingtests: ["typing", "test"],
};

const OPTIONAL_SPACE_PAIRS = new Set([
  "score|card",
  "cut|off",
  "answer|key",
  "admit|card",
  "hall|ticket",
  "response|sheet",
  "merit|list",
  "selection|list",
  "skill|test",
  "typing|test",
]);

const PLURALIZABLE_WORDS = new Set([
  "recruitment",
  "notification",
  "advertisement",
  "form",
  "date",
  "schedule",
  "card",
  "ticket",
  "intimation",
  "result",
  "list",
  "off",
  "key",
  "sheet",
  "verification",
  "test",
  "interview",
  "examination",
]);

function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function compileKeywordRegex(keyword: string): RegExp {
  const raw = (keyword || "").trim();
  if (!raw) {
    return /(?!x)x/;
  }
  const rawTokens = raw
    .toLowerCase()
    .split(/[\s_-]+/)
    .filter(Boolean);
  if (rawTokens.length === 0) {
    return /(?!x)x/;
  }

  const tokens: string[] = [];
  for (const tok of rawTokens) {
    if (COMPOUND_SPLITS[tok]) {
      tokens.push(...COMPOUND_SPLITS[tok]);
    } else {
      tokens.push(tok);
    }
  }

  const parts: string[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    const isLast = i === tokens.length - 1;
    let tokPat = "";
    if (isLast) {
      if (tok === "vacancy" || tok === "vacancies") {
        tokPat = "vacanc(?:y|ies)";
      } else if (tok === "posts") {
        tokPat = "posts";
      } else if (
        PLURALIZABLE_WORDS.has(tok) ||
        (tok.length >= 5 && tok.endsWith("s") && PLURALIZABLE_WORDS.has(tok.slice(0, -1)))
      ) {
        const base =
          tok.endsWith("s") && PLURALIZABLE_WORDS.has(tok.slice(0, -1))
            ? tok.slice(0, -1)
            : tok;
        tokPat = `${escapeRegExp(base)}s?`;
      } else {
        tokPat = escapeRegExp(tok);
      }
    } else {
      tokPat = escapeRegExp(tok);
    }
    parts.push(tokPat);

    if (!isLast) {
      const nextTok = tokens[i + 1];
      const nextBase =
        nextTok.endsWith("s") && PLURALIZABLE_WORDS.has(nextTok.slice(0, -1))
          ? nextTok.slice(0, -1)
          : nextTok;
      if (
        OPTIONAL_SPACE_PAIRS.has(`${tok}|${nextBase}`) ||
        OPTIONAL_SPACE_PAIRS.has(`${tok}|${nextTok}`)
      ) {
        parts.push("[\\s_./-]*");
      } else {
        parts.push("[\\s_./-]+");
      }
    }
  }

  return new RegExp(`(?<![A-Za-z0-9])(?:${parts.join("")})(?![A-Za-z0-9])`, "i");
}

export interface KeywordMatchResult {
  matched: boolean;
  category: string | null;
  keyword: string | null;
}

export function matchNoticeKeywords(
  title: string,
  content: string = "",
  categories: KeywordCategoryMap = KEYWORD_CATEGORIES
): KeywordMatchResult {
  const cleanTitle = (title || "").trim();
  const cleanContent = (content || "").trim();
  const combined = `${cleanTitle}\n${cleanContent}`.trim();
  if (!combined) {
    return { matched: false, category: null, keyword: null };
  }

  const lowerCombined = combined.toLowerCase().replace(/\s+/g, " ");
  if (NON_RECRUITMENT_NOISE_PHRASES.some((noise) => lowerCombined.includes(noise))) {
    return { matched: false, category: null, keyword: null };
  }

  for (const [category, kwList] of Object.entries(categories)) {
    const sorted = [...kwList].sort((a, b) => b.trim().length - a.trim().length);
    for (const kw of sorted) {
      const regex = compileKeywordRegex(kw);
      if (regex.test(cleanTitle) || (cleanContent && regex.test(cleanContent))) {
        return { matched: true, category, keyword: kw };
      }
    }
  }

  return { matched: false, category: null, keyword: null };
}
