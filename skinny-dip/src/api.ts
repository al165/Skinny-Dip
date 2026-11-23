// Thin client for the dip-args FastAPI server (dipa/api.py). Node ids are strings:
// "a…" argument, "i…" issue, "t…" theme, "p<n>" passage, "h:<slug>" holder.
// In dev, Vite proxies /api → localhost:8000 (see vite.config.ts).
// Set VITE_DIP_API (e.g. https://arranlyon.com/dip) to point a build elsewhere.
const BASE = ((import.meta.env.VITE_DIP_API as string | undefined) ?? "/api").replace(/\/$/, "");

export type Kind = "argument" | "issue" | "theme" | "holder" | "passage";
/** What the seed panel can list: node kinds, plus the image library. */
export type SeedKind = Kind | "image";

export type Holder = { id: string, name: string, type: string };
/** Where an argument comes from: one mention's document, quote and passage. */
export type Source = {
    document: string, url: string | null, quote: string, quote_verified: boolean,
    passage_id: string | null, voice: string,
};
/** "llm" = written by the model, "manual" = edited by a person, "verbatim" = source text. */
export type Provenance = "llm" | "manual" | "verbatim";

/** A row from GET /nodes, and the node part of every action result. */
export type Item = {
    id: string,
    kind: SeedKind,
    level: number,
    title: string,
    text: string,
    abstraction: number | null,
    /** Arguments: position | fact | example | recommendation | definition | question. */
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
    holder_type?: string,
    arguments?: number,
    children?: number,
    /** Images only (legacy, unused in dip-args). */
    url?: string | null,
    slug?: string,
    credit?: string | null,
    license?: string | null,
    source?: string | null,
};

/** A row from GET /image-library. */
type LibraryImage = {
    id: number,
    slug: string,
    title: string,
    description: string,
    subjects: string[],
    credit: string | null,
    source: string | null,
    creator: string | null,
    license: string | null,
    /** Relative to the API (e.g. /image-file/<slug>), not to the page. */
    url: string,
};

/** An API path as a URL the browser can load (the API sits under BASE, not at the page root). */
export const apiUrl = (path: string) => BASE + path;

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

/** One entry in an action's `results`. */
export type ActionResult = Item & {
    /** "image": a picture from the library (Images move); "node": everything else. */
    type: "node" | "image",
    score: number,
    why: string,
    match?: string,
    /** Images move: the metaphor and the node's concept that matched (metaphor route). */
    metaphor?: { keyword: string, gloss: string },
    concept?: string,
};

export type ActionResponse = {
    action: string,
    anchor: { id: string, kind: Kind | "image", title: string, text: string },
    results: ActionResult[],
    meta?: { fallback_used?: boolean, fallback_error?: string },
};

/** A source the answer drew on: a node result with its label in the answer ("A2") and whether it was cited. */
export type QuestionSource = ActionResult & { label: string, cited: boolean };

/** POST /question: an LLM answer that cites its material by label ([A2], [P1], [I1], [T1], [H1]). */
export type QuestionResponse = {
    question: string,
    selection: string | null,
    anchor: { id: string, kind: Kind, title: string, text: string } | null,
    answer: string,
    /** False when the material didn't answer the question, or the answer cited nothing. */
    found: boolean,
    /** Node ids of the cited material, in order of first citation. */
    cited: string[],
    /** Label -> node id, for every piece of material. */
    labels: Record<string, string>,
    /** Everything retrieved, cited first. */
    sources: QuestionSource[],
    meta: { seconds: number, cached: boolean, model: string, material: Record<string, number> },
};

export async function askQuestion(question: string, nodeId: string | null, selection: string | null): Promise<QuestionResponse> {
    const res = await fetch(BASE + "/question", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, node_id: nodeId, selection }),
    });
    if (!res.ok) {
        let detail: string | undefined;
        try { detail = (await res.json()).detail; } catch { /* not JSON */ }
        throw new Error(detail ?? `HTTP ${res.status}`);
    }
    return res.json() as Promise<QuestionResponse>;
}

export const RELATIONS = ["neighbours", "counter", "evidence", "zoom-in", "zoom-out", "shared-with", "sources",
    "arguments-here", "holder", "images"] as const;
/** The moves each kind of node offers (the API answers 400 for the others). */
export const MOVES: Record<string, readonly PassageRelation[]> = {
    argument: ["counter", "evidence", "neighbours", "zoom-in", "zoom-out", "shared-with", "sources", "holder", "images"],
    issue: ["zoom-in", "zoom-out", "neighbours", "images"],
    theme: ["zoom-in", "neighbours", "images"],
    holder: ["holder"],
    passage: ["arguments-here", "images"],
};
/** Moves offered on an image node. */
export const IMAGE_RELATIONS = ["image-passages"] as const;
export type PassageRelation = typeof RELATIONS[number];
/** Not API moves: "answer" links a card (or a corpus question) to its answer node,
 *  "cites" links an answer node to a source it cited. */
export type Relation = PassageRelation | typeof IMAGE_RELATIONS[number] | "answer" | "cites";

async function getJSON<T>(path: string): Promise<T> {
    const res = await fetch(BASE + path);
    if (!res.ok) {
        let detail: string | undefined;
        try { detail = (await res.json()).detail; } catch { /* not JSON */ }
        throw new Error(detail ?? `HTTP ${res.status}`);
    }
    return res.json() as Promise<T>;
}

export async function fetchNodes(kind: SeedKind, limit = 2000): Promise<Item[]> {
    if (kind === "image")
        return (await getJSON<LibraryImage[]>("/image-library")).map(imageToItem);
    return getJSON<Item[]>(`/nodes?kind=${kind}&limit=${limit}`);
}

/** The reverse of Images: passages a library image fits, by subject and by metaphor. */
export function runImagePassages(slug: string, k = 10) {
    return getJSON<ActionResponse>(`/image-passages/${encodeURIComponent(slug)}?k=${k}`);
}

export function runAction(relation: PassageRelation, nodeId: string, k = 10) {
    return getJSON<ActionResponse>(`/${relation}?node_id=${encodeURIComponent(nodeId)}&k=${k}`);
}
