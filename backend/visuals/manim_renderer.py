from __future__ import annotations

import asyncio
import json
from typing import Any

import manim_renderer as legacy_renderer

from .scene_builders import build_scene_render_payload
from .storyboard_schema import ScenePlan, Storyboard, clean_spaces


def trim_sentence(text: Any, limit: int = 56) -> str:
    value = clean_spaces(text)
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].strip()
    return (cut or value[:limit]).rstrip(".,;: ") + "..."


def coerce_storyboard(storyboard: Storyboard | dict[str, Any]) -> Storyboard:
    if isinstance(storyboard, Storyboard):
        return storyboard
    if isinstance(storyboard, dict):
        return Storyboard.from_dict(
            storyboard,
            subject=clean_spaces(storyboard.get("subject")),
            requested_depth=clean_spaces(storyboard.get("requested_depth")) or "normal",
            preferred_style=clean_spaces(storyboard.get("preferred_style")),
        )
    return Storyboard.from_dict({})


def select_storyboard_scene(
    storyboard: Storyboard | dict[str, Any],
    *,
    frame_number: int,
    total_segments: int = 0,
    segment_id: str = "",
) -> ScenePlan:
    board = coerce_storyboard(storyboard)
    scenes = board.scene_sequence or []
    if not scenes:
        return ScenePlan.from_dict({"scene_id": "scene_1", "scene_goal": "Introduce the core idea visually."})
    if segment_id:
        for scene in scenes:
            if clean_spaces(scene.segment_ref) == clean_spaces(segment_id):
                return scene
    if total_segments <= 0 or len(scenes) == total_segments:
        return scenes[min(max(frame_number - 1, 0), len(scenes) - 1)]
    if total_segments == 1:
        return scenes[0]
    ratio = max(0.0, min(1.0, (frame_number - 1) / max(1, total_segments - 1)))
    index = round(ratio * (len(scenes) - 1))
    return scenes[min(max(index, 0), len(scenes) - 1)]


def _scene_title(scene: ScenePlan) -> str:
    role = clean_spaces(scene.pedagogical_role).title()
    if role and role not in {"Scene", "Hook"}:
        return trim_sentence(f"{role}: {scene.scene_goal}", 42)
    return trim_sentence(scene.scene_goal, 42)


def storyboard_scene_to_payload(
    *,
    scene: ScenePlan,
    storyboard: Storyboard | dict[str, Any],
    question: str,
    lesson_plan: dict[str, Any] | None = None,
    segment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    board = coerce_storyboard(storyboard)
    context = {
        "question": question,
        "lesson_plan": lesson_plan or {},
        "segment": segment or {},
        "subject": board.subject,
    }
    builder_payload = build_scene_render_payload(scene, board, context)
    return {
        "renderer_version": "storyboard_v2",
        "scene_id": scene.scene_id,
        "segment_id": clean_spaces((segment or {}).get("segment_id")) or clean_spaces(scene.segment_ref),
        "scene_goal": scene.scene_goal,
        "scene_type": scene.scene_type,
        "scene_family": builder_payload.get("scene_family") or "comparison_transform",
        "subject": board.subject or context["subject"] or "generic",
        "title": _scene_title(scene),
        "subtitle": trim_sentence(scene.emphasis_points[0] if scene.emphasis_points else board.concept_summary, 72),
        "duration_sec": scene.estimated_duration,
        "layout_hint": scene.layout_hint,
        "camera_behavior": scene.camera_behavior,
        "pedagogical_role": scene.pedagogical_role,
        "text_usage": scene.text_usage,
        "equations_usage": scene.equations_usage,
        "transitions": scene.transitions,
        "key_visual_objects": list(scene.key_visual_objects),
        "emphasis_points": list(scene.emphasis_points),
        "animation_flow": list(scene.animation_flow),
        "data": builder_payload.get("data") or {},
    }


def build_storyboard_scene_source(scene_name: str, payload: dict[str, Any]) -> str:
    payload_literal = json.dumps(payload, ensure_ascii=False)
    latex_available_literal = "True" if legacy_renderer.manim_mathtex_allowed() else "False"
    template = '''from manim import *
import json
import numpy as np

config.frame_width = 14.222
config.frame_height = 8.0
config.pixel_width = 1280
config.pixel_height = 720

PAYLOAD = json.loads(PAYLOAD_LITERAL)
LATEX_AVAILABLE = __LATEX_AVAILABLE__
BG = "#0a0e13"
TXT = "#ecebe4"
MUT = "#9aa2ad"
ORANGE = "#e86c2f"
CYAN = "#7dd3fc"
GOLD = "#f2b84b"
GREEN = "#8ad17d"
PINK = "#ff8fab"


def fit_width(mob, width):
    if width and mob.width > width:
        mob.scale_to_fit_width(width)
    return mob


def smart_text(value, font_size=30, color=TXT, width=4.4):
    text = str(value or "").strip()
    if not text:
        return Text("", font_size=font_size, color=color)
    if not LATEX_AVAILABLE:
        mob = Text(text, font_size=max(18, int(font_size * 0.62)), color=color)
        return fit_width(mob, width)
    try:
        mob = MathTex(text, font_size=font_size, color=color)
    except Exception:
        mob = Text(text, font_size=max(18, int(font_size * 0.62)), color=color)
    return fit_width(mob, width)


def chip(label, color=CYAN):
    box = RoundedRectangle(corner_radius=0.16, width=2.25, height=0.56, stroke_color=color, stroke_width=2.2, fill_color="#15191f", fill_opacity=0.92)
    text = Text(str(label), font_size=20, color=TXT)
    fit_width(text, 1.95)
    text.move_to(box.get_center())
    return VGroup(box, text)


def card(title, lines, accent=ORANGE, width=3.25, height=2.4):
    box = RoundedRectangle(corner_radius=0.22, width=width, height=height, stroke_color=accent, stroke_width=2.8, fill_color="#15191f", fill_opacity=0.94)
    title_text = Text(str(title), font_size=26, color=accent)
    body = VGroup(*[chip(line, color=accent).scale(0.82) for line in lines]).arrange(DOWN, buff=0.14, aligned_edge=LEFT) if lines else VGroup()
    group = VGroup(title_text, body).arrange(DOWN, buff=0.24, aligned_edge=LEFT)
    fit_width(group, width - 0.4)
    group.move_to(box.get_center())
    return VGroup(box, group)


def focus_stack(items, width=4.1):
    rows = []
    for item in items:
        dot = Dot(radius=0.05, color=GOLD)
        line = Text(str(item), font_size=22, color=TXT)
        fit_width(line, width)
        rows.append(VGroup(dot, line).arrange(RIGHT, buff=0.16, aligned_edge=UP))
    return VGroup(*rows).arrange(DOWN, buff=0.2, aligned_edge=LEFT) if rows else VGroup()


def poly_path_from_points(axes, pairs, kind):
    points = [axes.c2p(pair[0], pair[1]) for pair in pairs]
    if len(points) < 2:
        points = [axes.c2p(0, 0), axes.c2p(1, 1)]
        pairs = [[0, 0], [1, 1]]
    if len(pairs) >= 3 and kind == "parabola":
        xs = [pair[0] for pair in pairs]
        ys = [pair[1] for pair in pairs]
        coeffs = np.polyfit(xs, ys, 2)
        return axes.plot(lambda x: coeffs[0] * x * x + coeffs[1] * x + coeffs[2], x_range=[min(xs), max(xs)], color=ORANGE, stroke_width=5.4)
    if len(pairs) >= 2 and kind == "line":
        xs = [pair[0] for pair in pairs]
        ys = [pair[1] for pair in pairs]
        coeffs = np.polyfit(xs, ys, 1)
        return axes.plot(lambda x: coeffs[0] * x + coeffs[1], x_range=[min(xs), max(xs)], color=ORANGE, stroke_width=5.4)
    path = VMobject(color=ORANGE, stroke_width=5.4)
    path.set_points_smoothly(points if len(points) >= 3 else [points[0], points[0] + RIGHT * 0.01, points[-1]])
    return path


class __SCENE_NAME__(MovingCameraScene):
    def construct(self):
        payload = PAYLOAD
        self.camera.background_color = BG
        title = Text(payload.get("title", "Visual scene"), font_size=32, color=TXT).to_edge(UP, buff=0.32)
        subtitle = Text(payload.get("subtitle", ""), font_size=21, color=MUT).next_to(title, DOWN, buff=0.14)
        heading = VGroup(title, subtitle)
        self.play(FadeIn(heading, shift=DOWN * 0.12), run_time=0.5)
        spent = 0.5
        duration = float(payload.get("duration_sec", 6.0))
        layout = str(payload.get("layout_hint", "center_morph") or "center_morph").strip().lower()
        family = str(payload.get("scene_family", "comparison_transform") or "comparison_transform").strip().lower()
        data = payload.get("data", {}) or {}
        emphasis = focus_stack(payload.get("emphasis_points", [])[:3], width=3.8).scale(0.86)

        if family == "graph_motion":
            axes = Axes(
                x_range=[-2, 6, 1],
                y_range=[-2, 6, 1],
                x_length=6.6,
                y_length=4.3,
                axis_config={"color": CYAN},
            )
            if layout == "left_visual_right_labels":
                axes.to_edge(LEFT, buff=0.55).shift(DOWN * 0.18)
            else:
                axes.move_to(DOWN * 0.2)
            x_label = Text(data.get("x_label", "x"), font_size=20, color=CYAN).next_to(axes.x_axis, RIGHT)
            y_label = Text(data.get("y_label", "y"), font_size=20, color=CYAN).next_to(axes.y_axis, UP)
            path = poly_path_from_points(axes, data.get("points", []), str(data.get("curve_kind", "smooth") or "smooth").lower())
            mover = Dot(path.get_start(), color=GOLD, radius=0.075)
            annotations = VGroup()
            for item in data.get("annotations", [])[:3]:
                point = axes.c2p(item.get("point", [0, 0])[0], item.get("point", [0, 0])[1])
                dot = Dot(point, color=GOLD, radius=0.07)
                lbl = Text(item.get("label", ""), font_size=18, color=GOLD).next_to(dot, UP if point[1] >= 0 else DOWN, buff=0.1)
                annotations.add(VGroup(dot, lbl))
            guides = VGroup()
            for idx, item in enumerate(data.get("guides", [])[:2]):
                start = axes.c2p(item.get("from", [0, 0])[0], item.get("from", [0, 0])[1])
                end = axes.c2p(item.get("to", [0, 0])[0], item.get("to", [0, 0])[1])
                color = CYAN if idx == 0 else GREEN
                line = Line(start, end, color=color, stroke_width=7)
                lbl = Text(item.get("label", ""), font_size=18, color=color).next_to(line, UP if idx == 0 else RIGHT, buff=0.1)
                guides.add(VGroup(line, lbl))
            eq_group = VGroup()
            equations = data.get("equation_sequence", []) or []
            if equations:
                eq_group = VGroup(*[smart_text(item, font_size=30, color=GOLD, width=3.8) for item in equations[:2]]).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
                if layout == "left_visual_right_labels":
                    eq_group.to_edge(RIGHT, buff=0.5).shift(UP * 0.75)
                    emphasis.next_to(eq_group, DOWN, buff=0.3)
                else:
                    eq_group.next_to(axes, RIGHT, buff=0.45).shift(UP * 0.75)
                    emphasis.next_to(eq_group, DOWN, buff=0.3)
            elif len(emphasis):
                emphasis.to_edge(RIGHT, buff=0.5).shift(DOWN * 0.25)

            self.play(Create(axes), FadeIn(x_label), FadeIn(y_label), run_time=0.7)
            spent += 0.7
            self.play(Create(path), run_time=0.8)
            spent += 0.8
            self.play(FadeIn(mover, scale=0.7), MoveAlongPath(mover, path), run_time=0.9, rate_func=linear)
            spent += 0.9
            if len(guides):
                self.play(LaggedStart(*[Create(item[0]) for item in guides], lag_ratio=0.12), LaggedStart(*[FadeIn(item[1], shift=UP * 0.06) for item in guides], lag_ratio=0.12), run_time=0.65)
                spent += 0.65
            if len(annotations):
                self.play(LaggedStart(*[FadeIn(item, scale=0.85) for item in annotations], lag_ratio=0.12), run_time=0.45)
                spent += 0.45
            if len(eq_group):
                self.play(LaggedStart(*[FadeIn(item, shift=LEFT * 0.08) for item in eq_group], lag_ratio=0.16), run_time=0.55)
                spent += 0.55
            if len(emphasis):
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.45)
                spent += 0.45
            if layout == "zoom_into_detail" and len(annotations):
                self.play(self.camera.frame.animate.scale(0.82).move_to(annotations[-1].get_center()), run_time=0.45)
                spent += 0.45
            self.wait(max(0.4, duration - spent))

        elif family == "trajectory_decomposition":
            axes = Axes(x_range=[0, 6, 1], y_range=[0, 4, 1], x_length=6.7, y_length=4.0, axis_config={"color": CYAN})
            axes.to_edge(LEFT if layout == "left_visual_right_labels" else LEFT, buff=0.55).shift(DOWN * 0.12)
            x_label = Text(data.get("x_label", "x"), font_size=20, color=CYAN).next_to(axes.x_axis, RIGHT)
            y_label = Text(data.get("y_label", "y"), font_size=20, color=CYAN).next_to(axes.y_axis, UP)
            path = poly_path_from_points(axes, data.get("trajectory_points", []), "parabola")
            mover = Dot(path.get_start(), color=GOLD, radius=0.08)
            markers = VGroup()
            for item in data.get("markers", [])[:3]:
                pt = axes.c2p(item.get("point", [0, 0])[0], item.get("point", [0, 0])[1])
                dot = Dot(pt, color=GOLD, radius=0.07)
                lbl = Text(item.get("label", ""), font_size=18, color=GOLD).next_to(dot, UP, buff=0.08)
                markers.add(VGroup(dot, lbl))
            vectors = VGroup()
            for idx, item in enumerate(data.get("vectors", [])[:3]):
                origin = axes.c2p(item.get("origin", [0, 0])[0], item.get("origin", [0, 0])[1])
                vec = item.get("vector", [1, 0])
                end = axes.c2p(item.get("origin", [0, 0])[0] + vec[0], item.get("origin", [0, 0])[1] + vec[1])
                color = [ORANGE, CYAN, GREEN][idx % 3]
                arrow = Arrow(origin, end, buff=0, color=color, stroke_width=5.6)
                lbl = Text(item.get("label", ""), font_size=18, color=color).next_to(arrow.get_end(), RIGHT if vec[0] >= 0 else LEFT, buff=0.1)
                vectors.add(VGroup(arrow, lbl))
            if len(emphasis):
                emphasis.to_edge(RIGHT, buff=0.45).shift(DOWN * 0.2)
            self.play(Create(axes), FadeIn(x_label), FadeIn(y_label), run_time=0.7)
            spent += 0.7
            self.play(Create(path), FadeIn(mover, scale=0.8), MoveAlongPath(mover, path), run_time=1.0, rate_func=linear)
            spent += 1.0
            if len(markers):
                self.play(LaggedStart(*[FadeIn(item, scale=0.85) for item in markers], lag_ratio=0.12), run_time=0.5)
                spent += 0.5
            if len(vectors):
                self.play(LaggedStart(*[GrowArrow(item[0]) for item in vectors], lag_ratio=0.14), LaggedStart(*[FadeIn(item[1], shift=UP * 0.06) for item in vectors], lag_ratio=0.14), run_time=0.85)
                spent += 0.85
            if len(emphasis):
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.4)
                spent += 0.4
            self.wait(max(0.4, duration - spent))

        elif family == "vector_decomposition":
            plane = NumberPlane(x_range=[-1, 5, 1], y_range=[-1, 5, 1], background_line_style={"stroke_color": "#20303c", "stroke_width": 1.0, "stroke_opacity": 0.48})
            plane.scale(0.9).move_to(LEFT * 2.0 + DOWN * 0.1)
            main = data.get("main_vector", [2.5, 2.2])
            main_arrow = Arrow(plane.c2p(0, 0), plane.c2p(main[0], main[1]), buff=0, color=ORANGE, stroke_width=6)
            main_label = Text("combined motion", font_size=20, color=ORANGE).next_to(main_arrow.get_end(), UR, buff=0.08)
            components = VGroup()
            for idx, item in enumerate(data.get("components", [])[:2]):
                vec = item.get("vector", [1, 0])
                color = CYAN if idx == 0 else GREEN
                arrow = Arrow(plane.c2p(0, 0), plane.c2p(vec[0], vec[1]), buff=0, color=color, stroke_width=5.2)
                lbl = Text(item.get("label", ""), font_size=19, color=color).next_to(arrow.get_end(), RIGHT if idx == 0 else UP, buff=0.08)
                components.add(VGroup(arrow, lbl))
            note = Text(data.get("result_label", ""), font_size=20, color=GOLD)
            fit_width(note, 3.4)
            note.to_edge(RIGHT, buff=0.45).shift(UP * 0.8)
            if len(emphasis):
                emphasis.to_edge(RIGHT, buff=0.45).shift(DOWN * 0.5)
            self.play(Create(plane), run_time=0.7)
            spent += 0.7
            self.play(GrowArrow(main_arrow), FadeIn(main_label, shift=UP * 0.08), run_time=0.65)
            spent += 0.65
            self.play(LaggedStart(*[GrowArrow(item[0]) for item in components], lag_ratio=0.18), LaggedStart(*[FadeIn(item[1], shift=UP * 0.06) for item in components], lag_ratio=0.18), run_time=0.8)
            spent += 0.8
            self.play(FadeIn(note, shift=UP * 0.08), run_time=0.35)
            spent += 0.35
            if len(emphasis):
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.4)
                spent += 0.4
            self.wait(max(0.4, duration - spent))

        elif family == "process_flow":
            nodes = data.get("nodes", [])[:6]
            boxes = VGroup(*[card(str(node), [], accent=ORANGE if idx == 0 else CYAN, width=2.35, height=1.2).scale(0.82) for idx, node in enumerate(nodes)])
            if layout == "top_bottom_causal_flow":
                boxes.arrange(DOWN, buff=0.36)
            elif layout == "radial_build":
                for idx, mob in enumerate(boxes):
                    angle = (TAU * idx / max(1, len(boxes))) + PI / 2
                    mob.move_to([2.4 * np.cos(angle), 1.6 * np.sin(angle) - 0.18, 0])
            else:
                boxes.arrange(RIGHT, buff=0.35)
            if layout != "radial_build":
                boxes.move_to(DOWN * 0.1)
            connectors = VGroup()
            labels = data.get("connectors", [])
            if layout == "radial_build":
                for idx in range(len(boxes)):
                    start = boxes[idx].get_center()
                    end = boxes[(idx + 1) % len(boxes)].get_center()
                    connectors.add(CurvedArrow(start, end, angle=-PI / 3, color=GOLD, stroke_width=4.6))
            else:
                for idx in range(len(boxes) - 1):
                    connectors.add(Arrow(boxes[idx].get_right() if layout != "top_bottom_causal_flow" else boxes[idx].get_bottom(), boxes[idx + 1].get_left() if layout != "top_bottom_causal_flow" else boxes[idx + 1].get_top(), buff=0.12, color=GOLD, stroke_width=4.8))
            self.play(LaggedStart(*[FadeIn(box, shift=UP * 0.1) for box in boxes], lag_ratio=0.16), run_time=0.8)
            spent += 0.8
            if len(connectors):
                self.play(LaggedStart(*[Create(conn) for conn in connectors], lag_ratio=0.14), run_time=0.6)
                spent += 0.6
            if labels and layout != "radial_build":
                note_group = VGroup()
                for idx, conn in enumerate(connectors[:len(labels)]):
                    txt = Text(str(labels[idx]), font_size=18, color=GOLD).next_to(conn, UP if layout != "top_bottom_causal_flow" else RIGHT, buff=0.08)
                    note_group.add(txt)
                self.play(FadeIn(note_group, shift=UP * 0.06), run_time=0.35)
                spent += 0.35
            if len(emphasis):
                emphasis.to_edge(RIGHT, buff=0.4).shift(DOWN * 0.2)
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.4)
                spent += 0.4
            self.wait(max(0.4, duration - spent))

        elif family == "cycle_flow":
            node_groups = VGroup()
            nodes = data.get("nodes", [])[:5]
            for idx, label in enumerate(nodes):
                angle = (TAU * idx / max(1, len(nodes))) + PI / 2
                mob = card(label, [], accent=ORANGE if idx == 0 else CYAN, width=2.2, height=1.0).scale(0.82)
                mob.move_to([2.45 * np.cos(angle), 1.9 * np.sin(angle) - 0.15, 0])
                node_groups.add(mob)
            arrows = VGroup()
            for idx in range(len(node_groups)):
                arrows.add(CurvedArrow(node_groups[idx].get_center(), node_groups[(idx + 1) % len(node_groups)].get_center(), angle=-PI / 3, color=GOLD, stroke_width=4.8))
            center = Circle(radius=0.78, color=GOLD, stroke_width=3).set_fill("#12181f", opacity=0.95)
            center_text = Text(data.get("center_label", "cycle"), font_size=22, color=GOLD).move_to(center.get_center())
            relation = Text(data.get("relationship_label", ""), font_size=20, color=MUT).next_to(center, DOWN, buff=1.0)
            fit_width(relation, 4.0)
            self.play(FadeIn(center), FadeIn(center_text), run_time=0.45)
            spent += 0.45
            self.play(LaggedStart(*[FadeIn(node, scale=0.88) for node in node_groups], lag_ratio=0.16), run_time=0.8)
            spent += 0.8
            self.play(LaggedStart(*[Create(arrow) for arrow in arrows], lag_ratio=0.12), FadeIn(relation, shift=UP * 0.06), run_time=0.7)
            spent += 0.7
            self.wait(max(0.4, duration - spent))

        elif family == "comparison_transform":
            left = card(data.get("left_title", "Before"), data.get("left_items", [])[:3], accent=ORANGE, width=3.55, height=2.9).move_to(LEFT * 3.0 + DOWN * 0.05)
            right = card(data.get("right_title", "After"), data.get("right_items", [])[:3], accent=CYAN, width=3.55, height=2.9).move_to(RIGHT * 3.0 + DOWN * 0.05)
            bridge = DoubleArrow(left.get_right(), right.get_left(), buff=0.22, color=GOLD, stroke_width=5.2)
            bridge_label = Text(data.get("bridge_label", ""), font_size=20, color=GOLD).next_to(bridge, UP, buff=0.12)
            equations = VGroup(*[smart_text(item, font_size=28, color=GOLD, width=4.0) for item in (data.get("equation_sequence", []) or [])[:2]]).arrange(DOWN, buff=0.18)
            if len(equations):
                equations.next_to(right, DOWN, buff=0.35)
            self.play(FadeIn(left, shift=RIGHT * 0.12), run_time=0.55)
            spent += 0.55
            self.play(FadeIn(right, shift=LEFT * 0.12), run_time=0.55)
            spent += 0.55
            self.play(Create(bridge), FadeIn(bridge_label, shift=UP * 0.06), run_time=0.45)
            spent += 0.45
            if len(equations):
                self.play(LaggedStart(*[FadeIn(item, shift=UP * 0.06) for item in equations], lag_ratio=0.14), run_time=0.45)
                spent += 0.45
            if len(emphasis):
                emphasis.to_edge(DOWN, buff=0.45)
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.4)
                spent += 0.4
            self.wait(max(0.4, duration - spent))

        elif family == "symbolic_transform":
            anchor = card(data.get("anchor_title", "Visual anchor"), data.get("anchor_items", [])[:3], accent=CYAN, width=4.4, height=2.9).move_to(LEFT * 2.4 + UP * 0.12)
            equations = data.get("equation_sequence", []) or []
            primary = smart_text(equations[0] if equations else "", font_size=36, color=GOLD, width=4.6)
            primary_frame = SurroundingRectangle(primary, buff=0.2, corner_radius=0.16, color=GOLD, stroke_width=2.4)
            primary_group = VGroup(primary_frame, primary).move_to(RIGHT * 2.35 + UP * 0.55)
            focus = focus_stack(data.get("focus_labels", [])[:3], width=3.5).scale(0.82).next_to(primary_group, DOWN, buff=0.3)
            self.play(FadeIn(anchor, shift=RIGHT * 0.1), run_time=0.55)
            spent += 0.55
            if equations:
                self.play(FadeIn(primary_frame), Write(primary), run_time=0.55)
                spent += 0.55
            if len(equations) > 1 and str(equations[1]).strip() and str(equations[1]).strip() != str(equations[0]).strip():
                secondary = smart_text(equations[1], font_size=34, color=TXT, width=4.6)
                secondary_frame = SurroundingRectangle(secondary, buff=0.2, corner_radius=0.16, color=CYAN, stroke_width=2.4)
                secondary_group = VGroup(secondary_frame, secondary).move_to(RIGHT * 2.35 + DOWN * 1.0)
                self.play(ReplacementTransform(primary_group.copy(), secondary_group), run_time=0.65)
                spent += 0.65
            if len(focus):
                self.play(LaggedStart(*[FadeIn(item, shift=UP * 0.06) for item in focus], lag_ratio=0.12), run_time=0.5)
                spent += 0.5
            self.wait(max(0.4, duration - spent))

        elif family == "geometry_build":
            pts = data.get("points", [[-3, -1.5], [3, -1.5], [1.5, 1.5]])
            poly = Polygon(*[np.array([pt[0], pt[1], 0]) for pt in pts], color=ORANGE, stroke_width=5.2)
            dots = VGroup(*[Dot(np.array([pt[0], pt[1], 0]), color=GOLD, radius=0.07) for pt in pts])
            labels = VGroup()
            for label, dot, direction in zip(data.get("labels", ["A", "B", "C"]), dots, [DL, DR, UP]):
                labels.add(Text(label, font_size=22, color=TXT).next_to(dot, direction, buff=0.08))
            self.play(Create(poly), run_time=0.75)
            spent += 0.75
            self.play(LaggedStart(*[FadeIn(dot, scale=0.8) for dot in dots], lag_ratio=0.12), LaggedStart(*[FadeIn(lbl, shift=UP * 0.06) for lbl in labels], lag_ratio=0.12), run_time=0.55)
            spent += 0.55
            for idx, item in enumerate(data.get("highlights", [])[:2]):
                if item.get("kind") == "side":
                    pair = item.get("indices", [0, 1])
                    start = np.array([pts[pair[0]][0], pts[pair[0]][1], 0])
                    end = np.array([pts[pair[1]][0], pts[pair[1]][1], 0])
                    line = Line(start, end, color=CYAN if idx == 0 else GREEN, stroke_width=8)
                    label = Text(item.get("label", ""), font_size=20, color=CYAN if idx == 0 else GREEN).next_to(line, UP, buff=0.08)
                    self.play(Create(line), FadeIn(label, shift=UP * 0.06), run_time=0.45)
                    spent += 0.45
            self.wait(max(0.4, duration - spent))

        elif family == "queue_frontier":
            node_map = {}
            graph_nodes = VGroup()
            for idx, item in enumerate(data.get("nodes", [])[:10]):
                pos = item.get("pos", [idx, 0])
                circle = Circle(radius=0.34, color=CYAN, stroke_width=3).set_fill("#12181f", opacity=0.94)
                circle.move_to(LEFT * 2.55 + np.array([pos[0], pos[1], 0]) * 0.75)
                label = Text(item.get("id", f"N{idx}"), font_size=22, color=TXT).move_to(circle.get_center())
                group = VGroup(circle, label)
                node_map[item.get("id", f"N{idx}")] = group
                graph_nodes.add(group)
            edges = VGroup()
            for left_id, right_id in data.get("edges", [])[:20]:
                if left_id in node_map and right_id in node_map:
                    edges.add(Line(node_map[left_id].get_center(), node_map[right_id].get_center(), color=MUT, stroke_width=2.6))
            queue_frame = RoundedRectangle(corner_radius=0.18, width=4.5, height=1.2, stroke_color=GOLD, fill_color="#15191f", fill_opacity=0.94).to_edge(RIGHT, buff=0.45).shift(UP * 1.25)
            queue_title = Text("Queue", font_size=24, color=GOLD).next_to(queue_frame, UP, buff=0.08)
            result = Text(data.get("result_label", ""), font_size=20, color=GOLD)
            fit_width(result, 4.2)
            result.next_to(queue_frame, DOWN, buff=0.25)
            if len(emphasis):
                emphasis.to_edge(RIGHT, buff=0.45).shift(DOWN * 1.0)
            self.play(Create(edges), LaggedStart(*[FadeIn(node, scale=0.85) for node in graph_nodes], lag_ratio=0.08), run_time=0.85)
            spent += 0.85
            self.play(FadeIn(queue_frame), FadeIn(queue_title, shift=UP * 0.06), run_time=0.35)
            spent += 0.35
            queue_states = data.get("queue_states", [])[:6]
            visit_order = data.get("visit_order", [])[:6]
            active_queue = VGroup()
            for step_index, state in enumerate(queue_states):
                queue_items = VGroup(*[chip(item, color=ORANGE if idx == 0 else CYAN).scale(0.68) for idx, item in enumerate(state[:4])]).arrange(RIGHT, buff=0.12)
                queue_items.move_to(queue_frame.get_center())
                animations = [FadeIn(queue_items, shift=UP * 0.04)] if step_index == 0 else [ReplacementTransform(active_queue, queue_items)]
                if step_index < len(visit_order) and visit_order[step_index] in node_map:
                    target = node_map[visit_order[step_index]][0]
                    animations.append(target.animate.set_fill(ORANGE, opacity=0.92).set_stroke(ORANGE))
                self.play(*animations, run_time=0.42)
                spent += 0.42
                active_queue = queue_items
            self.play(FadeIn(result, shift=UP * 0.06), run_time=0.3)
            spent += 0.3
            if len(emphasis):
                self.play(FadeIn(emphasis, shift=UP * 0.08), run_time=0.35)
                spent += 0.35
            self.wait(max(0.4, duration - spent))

        else:
            notice = Text(payload.get("scene_goal", "Visual explanation"), font_size=28, color=TXT)
            fit_width(notice, 9.0)
            self.play(FadeIn(notice, shift=UP * 0.08), run_time=0.7)
            spent += 0.7
            self.wait(max(0.4, duration - spent))
'''
    return template.replace("PAYLOAD_LITERAL", repr(payload_literal)).replace("__LATEX_AVAILABLE__", latex_available_literal).replace("__SCENE_NAME__", scene_name)


def render_manim_payload(
    payload: dict[str, Any],
    *,
    segment_id: str | None = None,
    frame_number: int | None = None,
) -> dict[str, Any]:
    if payload.get("renderer_version") != "storyboard_v2":
        return legacy_renderer.render_manim_payload(payload, segment_id=segment_id, frame_number=frame_number)

    key = legacy_renderer.payload_hash(payload)
    scene_name = f"ParalleaScene{key}"
    output_name = f"{key}.mp4" if legacy_renderer.MANIM_PUBLIC_BASE_URL.startswith("/rendered-scenes") else "scene.mp4"
    final_video = legacy_renderer.final_manim_video_path(key, output_name)
    work_dir = legacy_renderer.MANIM_WORK_DIR / key
    segment_label = clean_spaces(segment_id) or clean_spaces(payload.get("segment_id")) or f"frame_{frame_number or 0}"

    if not legacy_renderer.manim_storage_enabled() and final_video.exists() and final_video.stat().st_size > 0:
        scene_file = legacy_renderer.MANIM_SCENES_DIR / f"{key}.py"
        cache_bust = int(final_video.stat().st_mtime)
        media_url = f"{legacy_renderer.path_to_public_url(final_video)}?v={cache_bust}"
        return {
            "media_url": media_url,
            "video_url": media_url,
            "public_url": media_url,
            "media_path": str(final_video),
            "scene_source_path": str(scene_file) if scene_file.exists() else None,
            "cache_hit": True,
            "payload": payload,
        }

    return legacy_renderer.run_manim_scene(
        key=key,
        scene_name=scene_name,
        scene_source=build_storyboard_scene_source(scene_name, payload),
        output_name=output_name,
        final_video=final_video,
        segment_id=segment_label,
        frame_number=frame_number,
        payload=payload,
        work_dir=work_dir,
    )


async def render_manim_payload_async(
    payload: dict[str, Any],
    *,
    segment_id: str | None = None,
    frame_number: int | None = None,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        legacy_renderer.get_manim_render_executor(),
        lambda: render_manim_payload(payload, segment_id=segment_id, frame_number=frame_number),
    )


manim_runtime_info = legacy_renderer.manim_runtime_info
log_manim_runtime_status = legacy_renderer.log_manim_runtime_status
render_manim_healthcheck = legacy_renderer.render_manim_healthcheck
has_latex_available = legacy_renderer.has_latex_available
manim_mathtex_allowed = legacy_renderer.manim_mathtex_allowed
manim_allow_mathtex_effective_value = legacy_renderer.manim_allow_mathtex_effective_value
manim_text_only_mode = legacy_renderer.manim_text_only_mode
path_to_public_url = legacy_renderer.path_to_public_url
