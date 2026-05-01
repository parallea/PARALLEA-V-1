from manim import *

class GeneratedScene(Scene):
    def construct(self):
        # Step 1: Introduce binary fission
        cell = Circle(color=BLUE, radius=0.5).shift(LEFT)
        self.play(Create(cell))
        self.wait(1)
        # Step 2: Balloon analogy
        balloon = Circle(color=RED, radius=0.5).shift(RIGHT)
        self.play(Transform(cell, balloon))
        self.wait(1)
        # Step 3: Show prokaryotic cell
        prokaryote = Circle(color=GREEN, radius=0.5)
        self.play(Transform(balloon, prokaryote))
        self.wait(1)
        # Step 4: DNA duplication
        dna = Text('DNA', font_size=24).next_to(prokaryote, UP)
        self.play(Write(dna))
        self.wait(1)
        # Step 5: Cell elongation
        self.play(prokaryote.animate.scale(1.5))
        self.wait(1)
        # Step 6: Septum formation
        septum = Line(start=prokaryote.get_left(), end=prokaryote.get_right()).set_color(YELLOW)
        self.play(Create(septum))
        self.wait(1)
        # Step 7: Pinching off
        self.play(prokaryote.animate.shift(DOWN))
        self.wait(1)
        # Final recap
        self.play(FadeOut(dna), FadeOut(septum), FadeOut(prokaryote))
        self.wait(1)