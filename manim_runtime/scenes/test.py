from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Manim Render Test")
        circle = Circle()
        self.play(Write(title))
        self.play(Create(circle))
        self.wait(1)
