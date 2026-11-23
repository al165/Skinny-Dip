import { NodeResizer, type NodeProps } from "@xyflow/react";

import { IMAGE_RELATIONS } from "../api";
import type { ImageNode as ImageNodeType } from "../types";
import { Handles, MovesToolbar } from "./parts";

/**
 * Just the picture, filling the node; NodeResizer sets the node's size and the image follows it.
 * Only the width is set when it's added, so the height comes from the picture until the first resize;
 * keepAspectRatio then keeps that shape. The credit is in the tooltip.
 */
export function ImageNode({ id, data, selected, dragging }: NodeProps<ImageNodeType>) {
    const tooltip = [data.title, data.credit && `© ${data.credit}`, data.license].filter(Boolean).join("\n");

    return (
        <>
            <NodeResizer isVisible={selected && !dragging} keepAspectRatio minWidth={80} minHeight={40} />
            <Handles />
            <img className={`image-node${selected ? " selected" : ""}`} src={data.url} alt={data.title} title={tooltip} draggable={false} />
            <MovesToolbar id={id} moves={IMAGE_RELATIONS} pending={data.pending} />
        </>
    );
}
