from manim import *

class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("No-LaTeX fallback render", font_size=38, color=WHITE)
        title.scale_to_fit_width(10.6)
        title.to_edge(UP, buff=0.45)

        bullets = ["MathTex rejection smoke test 63ead58f"]
        rows = VGroup()
        for index, item in enumerate(bullets, start=1):
            dot = Dot(radius=0.06, color=BLUE)
            text = Text(str(index) + ". " + str(item), font_size=25, color=WHITE)
            text.scale_to_fit_width(8.7)
            rows.add(VGroup(dot, text).arrange(RIGHT, buff=0.2, aligned_edge=UP))
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.28)
        panel = Rectangle(width=10.4, height=4.2, color=BLUE, stroke_width=2.5)
        rows.move_to(panel.get_center())

        left = Circle(radius=0.36, color=GREEN)
        middle = Rectangle(width=1.35, height=0.66, color=YELLOW)
        right = Circle(radius=0.36, color=ORANGE)
        diagram = VGroup(left, middle, right).arrange(RIGHT, buff=0.7)
        diagram.next_to(panel, DOWN, buff=0.35)
        arrows = VGroup(
            Arrow(left.get_right(), middle.get_left(), buff=0.12, color=BLUE),
            Arrow(middle.get_right(), right.get_left(), buff=0.12, color=BLUE),
        )
        follow = Text("Does that make sense now?", font_size=24, color=YELLOW)
        follow.next_to(diagram, DOWN, buff=0.22)

        self.play(Write(title), run_time=0.7)
        self.play(Create(panel), Write(rows), run_time=1.0)
        self.play(FadeIn(diagram), Create(arrows), run_time=0.8)
        self.play(FadeIn(follow, shift=UP * 0.08), run_time=0.4)
        self.wait(2)
