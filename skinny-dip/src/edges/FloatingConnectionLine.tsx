import { getBezierPath, type ConnectionLineComponentProps, type InternalNode, type Node } from "@xyflow/react";

import { getEdgeParams } from "./floating";

/** The line drawn while dragging a new connection: floats from the source card to the cursor. */
export function FloatingConnectionLine({ toX, toY, fromPosition, toPosition, fromNode }: ConnectionLineComponentProps) {
    if (!fromNode) return null;

    // A 1×1 stand-in node at the cursor.
    const cursor = { id: "connection-target", measured: { width: 1, height: 1 }, internals: { positionAbsolute: { x: toX, y: toY } } };
    const { sx, sy, tx, ty, sourcePos, targetPos } = getEdgeParams(fromNode, cursor as InternalNode<Node>);

    const [path] = getBezierPath({
        sourceX: sx, sourceY: sy, sourcePosition: sourcePos || fromPosition,
        targetX: tx || toX, targetY: ty || toY, targetPosition: targetPos || toPosition,
    });

    return (
        <g>
            <path fill="none" stroke="#222" strokeWidth={1.5} className="animated" d={path} />
            <circle cx={tx || toX} cy={ty || toY} fill="#fff" r={3} stroke="#222" strokeWidth={1.5} />
        </g>
    );
}
