// Floating edges: where an edge meets each card, accounting for the card's tilt (data.rotation).
import { Position, type Node, type InternalNode } from '@xyflow/react';

type Point = { x: number, y: number };

// `measured` is only filled in once React Flow has rendered the node; until then treat it as 0×0.
const size = (n: InternalNode<Node>) => ({ width: n.measured.width ?? 0, height: n.measured.height ?? 0 });

// Cards are tilted by `.rotation-container` (`rotate(${data.rotation}deg)`, CSS default origin = centre).
// Nodes without a rotation (notes, the connection line's cursor stand-in) count as 0°.
const rotationRad = (n: InternalNode<Node>) => {
    const deg = (n.data as { rotation?: unknown } | undefined)?.rotation;
    return typeof deg === 'number' ? (deg * Math.PI) / 180 : 0;
};

const center = (n: InternalNode<Node>): Point => {
    const { x, y } = n.internals.positionAbsolute;
    const { width, height } = size(n);
    return { x: x + width / 2, y: y + height / 2 };
};

// Rotate `p` by `angle` (radians, clockwise on screen since y points down) about `origin`.
const rotate = (p: Point, origin: Point, angle: number): Point => {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const dx = p.x - origin.x;
    const dy = p.y - origin.y;
    return { x: origin.x + dx * cos - dy * sin, y: origin.y + dx * sin + dy * cos };
};

/**
 * Where the line from `intersectionNode`'s centre towards `targetNode`'s centre leaves
 * `intersectionNode`'s (rotated) box. Worked out in the node's own unrotated frame, then rotated back.
 */
function getNodeIntersection(intersectionNode: InternalNode<Node>, targetNode: InternalNode<Node>): Point {
    const c = center(intersectionNode);
    const angle = rotationRad(intersectionNode);
    const w = size(intersectionNode).width / 2;
    const h = size(intersectionNode).height / 2;

    // The other node's centre, seen from this node's unrotated frame.
    const t = rotate(center(targetNode), c, -angle);
    const dx = t.x - c.x;
    const dy = t.y - c.y;

    // Scale the centre-to-target vector until it touches the box edge.
    // (Degenerate cases — zero size, or both centres coincide — fall back to the centre.)
    const scale = Math.max(w > 0 ? Math.abs(dx) / w : 0, h > 0 ? Math.abs(dy) / h : 0);
    const local = scale > 0 ? { x: c.x + dx / scale, y: c.y + dy / scale } : c;

    return rotate(local, c, angle);
}

/** The side the edge leaves from, as the screen direction closest to that side's (rotated) outward normal. */
function getEdgePosition(node: InternalNode<Node>, intersectionPoint: Point): Position {
    const c = center(node);
    const angle = rotationRad(node);
    const { width, height } = size(node);

    // Which side of the unrotated box the point is on.
    const p = rotate(intersectionPoint, c, -angle);
    const nx = width > 0 ? (p.x - c.x) / (width / 2) : 0;
    const ny = height > 0 ? (p.y - c.y) / (height / 2) : 0;
    const normal = Math.abs(nx) >= Math.abs(ny)
        ? { x: Math.sign(nx) || 1, y: 0 }
        : { x: 0, y: Math.sign(ny) || -1 };

    // Rotate that side's outward normal into screen space and snap to the nearest cardinal direction.
    const n = rotate(normal, { x: 0, y: 0 }, angle);
    if (Math.abs(n.x) >= Math.abs(n.y))
        return n.x < 0 ? Position.Left : Position.Right;
    return n.y < 0 ? Position.Top : Position.Bottom;
}

export function getEdgeParams(source: InternalNode<Node>, target: InternalNode<Node>) {
    const sourceIntersectionPoint = getNodeIntersection(source, target);
    const targetIntersectionPoint = getNodeIntersection(target, source);

    const sourcePos = getEdgePosition(source, sourceIntersectionPoint);
    const targetPos = getEdgePosition(target, targetIntersectionPoint);

    return {
        sx: sourceIntersectionPoint.x,
        sy: sourceIntersectionPoint.y,
        tx: targetIntersectionPoint.x,
        ty: targetIntersectionPoint.y,
        sourcePos,
        targetPos,
    };
}

export function customBezierEdge({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
}: { sourceX: number, sourceY: number, targetX: number, targetY: number, sourcePosition: Position, targetPosition: Position }): [string, number, number] {
    const offset = (pos: Position, d: number) =>
        pos === Position.Left ? { x: -d, y: 0 } : pos === Position.Right ? { x: d, y: 0 }
            : pos === Position.Top ? { x: 0, y: -d } : { x: 0, y: d };

    const dx = targetX - sourceX;
    const dy = targetY - sourceY;
    const lenSq = dx * dx + dy * dy;
    const diagonal = lenSq > 0 ? (2 * Math.abs(dx * dy)) / lenSq : 0;

    const curvature = 1.1 * diagonal;
    const d = Math.hypot(targetX - sourceX, targetY - sourceY) * curvature;
    const c1 = offset(sourcePosition, d), c2 = offset(targetPosition, d);
    const path = `M${sourceX},${sourceY} C${sourceX + c1.x},${sourceY + c1.y} ${targetX + c2.x},${targetY + c2.y} ${targetX},${targetY}`;
    const labelX = (sourceX + targetX) / 2;
    const labelY = (sourceY + targetY) / 2;

    return [path, labelX, labelY];
}
