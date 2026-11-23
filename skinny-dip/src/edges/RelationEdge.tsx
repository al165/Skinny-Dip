import { BaseEdge, EdgeLabelRenderer, getBezierPath, useInternalNode, type EdgeProps } from "@xyflow/react";

import { RELATION_STYLE } from "../relations";
import type { RelationEdge as RelationEdgeType } from "../types";
import { getEdgeParams } from "./floating";

/** A floating edge (it joins the nearest points of the two tilted cards) with its relation's label. */
export function RelationEdge({ id, source, target, data, markerEnd, style }: EdgeProps<RelationEdgeType>) {
    const sourceNode = useInternalNode(source);
    const targetNode = useInternalNode(target);
    if (!sourceNode || !targetNode) return null;

    const { sx, sy, tx, ty, sourcePos, targetPos } = getEdgeParams(sourceNode, targetNode);
    const [path, labelX, labelY] = getBezierPath({
        sourceX: sx, sourceY: sy, sourcePosition: sourcePos,
        targetX: tx, targetY: ty, targetPosition: targetPos,
    });
    const relation = data ? RELATION_STYLE[data.relation] : null;

    return (
        <>
            <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
            {relation?.label &&
                <EdgeLabelRenderer>
                    <div className="edge-label nodrag nopan" title={`${data?.why ?? ""} · score ${data?.score ?? ""}`}
                        style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, background: relation.colour }}>
                        {relation.label}
                    </div>
                </EdgeLabelRenderer>}
        </>
    );
}
