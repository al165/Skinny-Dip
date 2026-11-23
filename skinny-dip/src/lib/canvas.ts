// Canvas ids and small node-list helpers shared by the store and the components.
import type { ActionResult } from "../api";
import { NODE_WIDTH } from "../relations";
import type { NodeType } from "../types";

// --- ids ---------------------------------------------------------------------------------

/** One canvas node per DIP node, so two paths that reach the same argument converge on it. */
export const flowId = (dipId: string) => `n${dipId}`;

/** A seed on the canvas is split in two nodes that share one DIP node (same `dipId`, so every move works
 *  on either): the title card, and the description card it can expand into. Suffixed ids keep them apart
 *  from each other and from the plain card (`flowId`) the same DIP node gets when it comes back as a result. */
export const seedTitleId = (dipId: string) => `${flowId(dipId)}-title`;
export const seedTextId = (dipId: string) => `${flowId(dipId)}-text`;

/** Images are keyed by slug, a separate namespace from node ids, so they get their own prefix. */
export const imageFlowId = (slug: string) => `img${slug}`;

/** The canvas id an action result gets: images by slug, everything else by node id. */
export const resultFlowId = (r: ActionResult) => (r.type === "image" ? imageFlowId(r.slug ?? r.id) : flowId(r.id));

// --- nodes -------------------------------------------------------------------------------

/** Copy of `nodes` with only `id` selected (or none). */
export const selectOnly = (nodes: NodeType[], id?: string): NodeType[] =>
    nodes.map(n => ({ ...n, selected: n.id === id }) as NodeType);

/** A node's rendered size, or a guess until React Flow has measured it. */
export const sizeOf = (n: NodeType, fallbackHeight = 200) =>
    ({ w: n.measured?.width ?? n.width ?? NODE_WIDTH, h: n.measured?.height ?? fallbackHeight });

/** Image nodes sit behind cards and edges; xyflow adds 1000 to a selected node, which lifts it to the front. */
export const IMAGE_Z = -1;

const ROTATION_AMOUNT = 15;

/** A small random tilt (±7.5°) so cards on the canvas look placed by hand. */
export const randomRotation = () => Math.random() * ROTATION_AMOUNT - ROTATION_AMOUNT / 2;
