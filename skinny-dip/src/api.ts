// Thin client for the dip-args FastAPI server (dipa/api.py), and the shapes it returns.
// Node ids are strings: "a…" argument, "i…" issue, "t…" theme, "p<n>" passage, "h:<slug>" holder.
// In dev, Vite proxies /api → the server (see vite.config.ts); set VITE_DIP_API to point a build elsewhere.
const BASE = ((import.meta.env.VITE_DIP_API as string | undefined) ?? "/api").replace(/\/$/, "");

// --- types -------------------------------------------------------------------------------

export type Kind = "argument" | "issue" | "theme" | "holder" | "passage";
/** What the seeds panel can list: node kinds, plus the image library. */
export type SeedKind = Kind | "image";

export type Holder = { id: string, name: string, type: string };

/** Where an argument comes from: one mention's document, quote and passage. */
export type Source = {
    document: string, url: string | null, quote: string, quote_verified: boolean,
    passage_id: string | null, voice: string,
};

/** "llm" = written by the model, "manual" = edited by a person, "verbatim" = source text. */
export type Provenance = "llm" | "manual" | "verbatim";

/** A row from GET /nodes (or the image library, see `imageToItem`), and the node part of every action result. */
export type Item = {
    id: string,
    kind: SeedKind,
    level: number,
    title: string,
    text: string,
    abstraction: number | null,
    /** Arguments: position | fact | example | recommendation | definition | question. Images: "image". */
    role?: string | null,
    provenance?: Provenance,
    model?: string | null,
    /** Title of the original document ("3 documents" when an argument has several). */
    document: string | null,
    /** URL of the original document; null for issues, themes and holders. */
    source_url: string | null,
    holder?: Holder | null,
    /** How each document treats an argument: rejected / endorsed / reported / argued. */
    reception?: { document: string, voice: string, url?: string | null }[],
    sources?: Source[],
    /** Passages from Sources: the quote to highlight. */
    highlight?: { quote: string, start: number | null, end: number | null, verified: boolean },
    /** Images only. */
    url?: string | null,
    slug?: string,
    credit?: string | null,
    license?: string | null,
};

/** One entry in an action's `results`. */
export type ActionResult = Item & {
    /** "image": a picture from the library (Images move); "node": everything else. */
    type: "node" | "image",
    score: number,
    why: string,
};

export type ActionResponse = {
    action: string,
    results: ActionResult[],
    meta?: { fallback_used?: boolean, fallback_error?: string },
};

/** A source the answer drew on: a node result with its label in the answer ("A2") and whether it was cited. */
export type QuestionSource = ActionResult & { label: string, cited: boolean };

/** POST /question: an LLM answer that cites its material by label ([A2], [P1], [I1], [T1], [H1]). */
export type QuestionResponse = {
    question: string,
    selection: string | null,
    answer: string,
    /** False when the material didn't answer the question, or the answer cited nothing. */
    found: boolean,
    /** Everything retrieved, cited first. */
    sources: QuestionSource[],
    meta: { seconds: number, cached: boolean, model: string },
};

/** A row from GET /image-library. */
type LibraryImage = {
    id: number,
    slug: string,
    title: string,
    description: string,
    credit: string | null,
    source: string | null,
    license: string | null,
    /** Relative to the API (e.g. /image-file/<slug>), not to the page. */
    url: string,
};

// --- moves -------------------------------------------------------------------------------

export const RELATIONS = ["neighbours", "counter", "evidence", "zoom-in", "zoom-out", "shared-with", "sources",
    "arguments-here", "holder", "images"] as const;
export type PassageRelation = typeof RELATIONS[number];

/** Moves offered on an image node. */
export const IMAGE_RELATIONS = ["image-passages"] as const;
export type ImageRelation = typeof IMAGE_RELATIONS[number];

/** The moves each kind of node offers (the API answers 400 for the others). */
export const MOVES: Record<string, readonly PassageRelation[]> = {
    argument: ["counter", "evidence", "neighbours", "zoom-in", "zoom-out", "shared-with", "sources", "holder", "images"],
    issue: ["zoom-in", "zoom-out", "neighbours", "images"],
    theme: ["zoom-in", "neighbours", "images"],
    holder: ["holder"],
    passage: ["arguments-here", "images"],
};

// --- requests ----------------------------------------------------------------------------

/** An API path as a URL the browser can load (the API sits under BASE, not at the page root). */
export const apiUrl = (path: string) => BASE + path;

/** Fetch JSON from the API; a failed request throws with the server's `detail` when it sent one. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(BASE + path, init);
    if (!res.ok) {
        let detail: string | undefined;
        try { detail = (await res.json()).detail; } catch { /* not JSON */ }
        throw new Error(detail ?? `HTTP ${res.status}`);
    }
    return res.json() as Promise<T>;
}

/** Image library rows in the seed shape, so the panel can list, filter and drag them like nodes. */
function imageToItem(img: LibraryImage): Item {
    return {
        id: String(img.id),
        kind: "image", level: 0, abstraction: null,
        title: img.title,
        text: img.description,
        role: "image",
        document: null,
        source_url: img.source,
        url: apiUrl(img.url),
        slug: img.slug,
        credit: img.credit,
        license: img.license,
    };
}

export async function fetchNodes(kind: SeedKind, limit = 2000): Promise<Item[]> {
    if (kind === "image")
        return (await request<LibraryImage[]>("/image-library")).map(imageToItem);
    return request<Item[]>(`/nodes?kind=${kind}&limit=${limit}`);
}

export function runAction(relation: PassageRelation, nodeId: string, k = 10) {
    return request<ActionResponse>(`/${relation}?node_id=${encodeURIComponent(nodeId)}&k=${k}`);
}

/** The reverse of Images: passages a library image fits, by subject and by metaphor. */
export function runImagePassages(slug: string, k = 10) {
    return request<ActionResponse>(`/image-passages/${encodeURIComponent(slug)}?k=${k}`);
}

export function askQuestion(question: string, nodeId: string | null, selection: string | null) {
    return request<QuestionResponse>("/question", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, node_id: nodeId, selection }),
    });
}
