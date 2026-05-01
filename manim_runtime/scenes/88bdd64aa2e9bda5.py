from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("No-LaTeX Manim Render", font_size=42)
        eq = Text("v = u + a t", font_size=34)
        line = Text("Range = u^2 sin(2 theta) / g", font_size=28)
        group = VGroup(title, eq, line).arrange(DOWN, buff=0.45)
        self.play(Write(title))
        self.play(FadeIn(eq, shift=UP * 0.2))
        self.play(FadeIn(line, shift=UP * 0.2))
        box = Rectangle(width=11, height=4.6, color=BLUE)
        self.play(Create(box))
        self.wait(1)