// Pure constructors for canvas nodes and edges, and the call behind each move. No store state here.
import { MarkerType, type XYPosition } from "@xyflow/react";

import { runAction, runImagePassages, type Item } from "../api";
import { IMAGE_Z, randomRotation } from "../lib/canvas";
import { RELATION_STYLE } from "../relations";
import {
    isImage, isPassage,
    type ImageNode, type NoteNode, type PassageData, type PassageNode, type Relation, type RelationEdge,
} from "../types";

export const NOTE_COLOURS = ["#4D4034", "#B56936", "#614956", "#254369", "#508C65", "#716994"];
export const NOTE_FONT_SIZES = [12, 14, 18, 24, 32, 48];
export const NOTE_DEFAULT_SIZE = 14;
const NOTE_SIZE = { width: 200, height: 70 };
const IMAGE_WIDTH = 200;

/** Card data from a seed or an action result. */
export function toPassageData(r: Item): PassageData {
    return {
        dipId: r.id,
        kind: r.kind as PassageData["kind"],
        title: r.title,
        text: r.text,
        role: r.role ?? null,
        abstraction: r.abstraction ?? null,
        document: r.document ?? null,
        sourceUrl: r.source_url ?? null,
        provenance: r.provenance,
        model: r.model ?? null,
        holder: r.holder ?? null,
        reception: r.reception ?? [],
        sources: r.sources ?? [],
        highlight: r.highlight,
        rotation: randomRotation(),
    };
}

export const passageNode = (id: string, position: XYPosition, data: PassageData): PassageNode =>
    ({ id, type: "passage", position, data, selected: false });

/** An image node; `url` must already be absolute. Only the width is set: the height follows the picture. */
export const imageNode = (id: string, position: XYPosition, item: Item, url: string): ImageNode => ({
    id, type: "image", position, selected: false, width: IMAGE_WIDTH, zIndex: IMAGE_Z,
    data: {
        slug: item.slug ?? item.id, title: item.title, description: item.text, url,
        credit: item.credit ?? null, license: item.license ?? null, sourceUrl: item.source_url ?? null,
        rotation: randomRotation(),
    },
});

/** An empty note centred on `at`; width/height make it resizable (NodeResizer updates them). */
export const noteNode = (at: XYPosition): NoteNode => ({
    id: `note-${crypto.randomUUID()}`, type: "note",
    position: { x: at.x - NOTE_SIZE.width / 2, y: at.y - NOTE_SIZE.height / 2 },
    ...NOTE_SIZE,
    data: { colour: NOTE_COLOURS[0], text: "", fontSize: NOTE_DEFAULT_SIZE }, selected: true,
});

/** An edge styled by its relation (see RELATION_STYLE). */
export function makeEdge(source: string, target: string, relation: Relation, r: { why: string, score: number }): RelationEdge {
    const style = RELATION_STYLE[relation];
    return {
        id: `${source}-${relation}-${target}`,
        source, target,
        sourceHandle: style.sourceHandle,
        targetHandle: style.targetHandle,
        type: "relation",
        data: { relation, why: r.why, score: r.score },
        markerEnd: style.markerProps === null ? undefined
            : {
                type: MarkerType.Arrow,
                color: "var(--arrow)",
                height: 20,
                width: 20,
                ...style.markerProps,
            },
        style: {
            stroke: "var(--arrow)",
            strokeWidth: style.strokeWidth ?? 2,
            ...(style.strokeDasharray && { strokeDasharray: style.strokeDasharray, strokeLinecap: "round" as const }),
        },
    };
}

/** Run `relation` on a canvas node: image moves need an image, the others a DIP node. */
export function fetchAction(source: PassageNode | ImageNode, relation: Relation) {
    if (relation === "image-passages") {
        if (!isImage(source)) throw new Error("Passages only applies to images");
        return runImagePassages(source.data.slug);
    }
    if (relation === "answer" || relation === "cites" || relation === "description") throw new Error(`${relation} is not a move`);
    if (!isPassage(source)) throw new Error(`${relation} does not apply to images`);
    return runAction(relation, source.data.dipId);
}
