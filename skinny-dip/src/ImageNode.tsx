import { Handle, NodeResizer, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import type { CSSProperties } from "react";

import { IMAGE_RELATIONS } from "./api";
import { RELATION_STYLE } from "./relations";
import { useGraph, type ImageNode as ImageNodeType } from "./store/graphs";
import { SIDES } from "./utils";

/**
 * Just the picture, filling the node; NodeResizer sets the node's size and the image follows it.
 * Only the width is set when it's added, so the height comes from the picture until the first resize;
 * keepAspectRatio then keeps that shape. The credit is in the tooltip.
 */
export function ImageNode({ id, data, selected, dragging }: NodeProps<ImageNodeType>) {
    const expand = useGraph(s => s.expand);
    const tooltip = [data.title, data.credit && `© ${data.credit}`, data.license].filter(Boolean).join("\n");

    return (
        <>
            <NodeResizer isVisible={selected && !dragging} keepAspectRatio minWidth={80} minHeight={40} />
            {/* Invisible handles on every side; each relation picks a pair (see relations.ts). */}
            {SIDES.map(([side, pos]) => (
                <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} isConnectable={false} />
            ))}
            {SIDES.map(([side, pos]) => (
                <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} isConnectable={false} />
            ))}
            <img
                className={`image-node${selected ? " selected" : ""}`}
                src={data.url} alt={data.title} title={tooltip} draggable={false}
            />
            <NodeToolbar position={Position.Bottom} align="center">
                <div className="actions nodrag">
                    {IMAGE_RELATIONS.map(r => {
                        const style = RELATION_STYLE[r];
                        return (
                            <button
                                key={r}
                                title={style.hint}
                                disabled={!!data.pending}
                                className={data.pending === r ? "pending" : ""}
                                style={{ "--c": style.colour } as CSSProperties}
                                onClick={() => expand(id, r)}
                            >
                                {style.label}
                            </button>
                        );
                    })}
                </div>
            </NodeToolbar>
        </>
    );
}
