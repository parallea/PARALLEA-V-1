from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Clarification: projectile range", font_size=38, color=WHITE)
        title.to_edge(UP, buff=0.5)
        eq = Text("Range = u^2 sin(2 theta) / g", font_size=30, color=YELLOW)
        eq.next_to(title, DOWN, buff=0.5)
        bullets = VGroup(
            Text("1. The launch speed u sets the energy.", font_size=24),
            Text("2. The angle theta sets the spread between height and distance.", font_size=24),
            Text("3. Gravity g pulls everything down at the same rate.", font_size=24),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.3)
        bullets.next_to(eq, DOWN, buff=0.6)
        box = Rectangle(width=12, height=2.8, color=BLUE).move_to(bullets)
        self.play(Write(title))
        self.play(FadeIn(eq, shift=UP * 0.2))
        self.play(Create(box), FadeIn(bullets, shift=UP * 0.2))
        self.wait(1.2)