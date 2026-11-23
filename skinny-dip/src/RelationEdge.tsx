import { BaseEdge, EdgeLabelRenderer, getBezierPath, useInternalNode, type EdgeProps } from "@xyflow/react";
import { getEdgeParams } from "./floatingEdgeUtils";

import { RELATION_STYLE } from "./relations";
import type { RelationEdge as RelationEdgeType } from "./store/graphs";

export function RelationEdge(props: EdgeProps<RelationEdgeType>) {
    const sourceNode = useInternalNode(props.source);
    const targetNode = useInternalNode(props.target);

    if (!sourceNode || !targetNode) {
        return null;
    }

    const { sx, sy, tx, ty, sourcePos, targetPos } = getEdgeParams(sourceNode, targetNode);

    const [path, labelX, labelY] = getBezierPath({
        sourceX: sx,
        sourceY: sy,
        sourcePosition: sourcePos,
        targetPosition: targetPos,
        targetX: tx,
        targetY: ty,
    });
    const relation = props.data?.relation;
    const style = relation ? RELATION_STYLE[relation] : null;

    return (
        <>
            <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={props.style} />
            {style?.label && (
                <EdgeLabelRenderer>
                    <div
                        className="edge-label nodrag nopan"
                        title={`${props.data?.why ?? ""} · score ${props.data?.score ?? ""}`}
                        style={{
                            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
                            background: style.colour,
                        }}
                    >
                        {style.label}
                    </div>
                </EdgeLabelRenderer>
            )}
        </>
    );
}
