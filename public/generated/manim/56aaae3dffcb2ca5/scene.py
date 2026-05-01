from manim import *

class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Interactive clarification", font_size=34, color=WHITE)
        title.to_edge(UP, buff=0.45)
        cause = Circle(radius=0.42, color=BLUE).shift(LEFT * 3)
        change = Rectangle(width=1.35, height=0.78, color=GREEN)
        result = Circle(radius=0.42, color=ORANGE).shift(RIGHT * 3)
        arrow_a = Arrow(cause.get_right(), change.get_left(), buff=0.18, color=YELLOW)
        arrow_b = Arrow(change.get_right(), result.get_left(), buff=0.18, color=YELLOW)
        dot = Dot(cause.get_center(), radius=0.08, color=YELLOW)
        caption = Text("Cause moves through a change", font_size=24, color=WHITE)
        caption.to_edge(DOWN, buff=0.7)
        self.play(Write(title), FadeIn(caption), run_time=0.7)
        self.play(Create(cause), FadeIn(dot), run_time=0.7)
        self.play(Create(arrow_a), dot.animate.move_to(change.get_center()), run_time=0.9)
        self.play(Create(change), Indicate(change, color=YELLOW), run_time=0.7)
        self.play(Create(arrow_b), dot.animate.move_to(result.get_center()), run_time=0.9)
        self.play(Create(result), Circumscribe(VGroup(cause, change, result), color=YELLOW), run_time=0.8)
        self.wait(0.8)