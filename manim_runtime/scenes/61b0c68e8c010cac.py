from manim import *

class ParalleaGeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Cause to result", font_size=34, color=WHITE).to_edge(UP, buff=0.45)
        left = Circle(radius=0.42, color=BLUE).shift(LEFT * 3)
        mid = Rectangle(width=1.35, height=0.72, color=GREEN)
        right = Circle(radius=0.42, color=ORANGE).shift(RIGHT * 3)
        dot = Dot(left.get_center(), radius=0.08, color=YELLOW)
        arrow_a = Arrow(left.get_right(), mid.get_left(), buff=0.15, color=YELLOW)
        arrow_b = Arrow(mid.get_right(), right.get_left(), buff=0.15, color=YELLOW)
        caption = Text("Watch the change move step by step", font_size=24, color=WHITE).to_edge(DOWN, buff=0.7)
        self.play(Write(title), FadeIn(caption), run_time=0.6)
        self.play(Create(left), FadeIn(dot), run_time=0.6)
        self.play(Create(arrow_a), dot.animate.move_to(mid.get_center()), run_time=0.8)
        self.play(Create(mid), Indicate(mid, color=YELLOW), run_time=0.6)
        self.play(Create(arrow_b), dot.animate.move_to(right.get_center()), run_time=0.8)
        self.play(Create(right), Circumscribe(VGroup(left, mid, right), color=YELLOW), run_time=0.7)
        self.wait(0.6)