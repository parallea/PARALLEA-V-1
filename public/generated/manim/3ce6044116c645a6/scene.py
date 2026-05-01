from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Why ML needs math", font_size=40, color=WHITE).to_edge(UP)
        subtitle = Text("Ayush, the computer analyzes the data after it becomes numbers.", font_size=24, color=GRAY).next_to(title, DOWN)

        raw_label = Text("Raw data", font_size=28, color=BLUE)
        raw_box = RoundedRectangle(corner_radius=0.2, width=3.0, height=2.0, color=BLUE)
        dog = Text("🐶", font_size=72)
        raw_group = VGroup(raw_box, raw_label, dog).arrange(DOWN, buff=0.2)
        raw_group.move_to(LEFT * 4.0 + DOWN * 0.2)

        number_label = Text("Numbers", font_size=28, color=YELLOW)
        number_box = RoundedRectangle(corner_radius=0.2, width=3.0, height=2.0, color=YELLOW)
        nums = Text("[12, 88, 34, ...]", font_size=34, color=YELLOW)
        num_group = VGroup(number_box, number_label, nums).arrange(DOWN, buff=0.2)
        num_group.move_to(ORIGIN + DOWN * 0.2)

        model_label = Text("ML model", font_size=28, color=GREEN)
        model_box = RoundedRectangle(corner_radius=0.2, width=3.0, height=2.0, color=GREEN)
        decision = Text("Dog", font_size=40, color=GREEN)
        model_group = VGroup(model_box, model_label, decision).arrange(DOWN, buff=0.2)
        model_group.move_to(RIGHT * 4.0 + DOWN * 0.2)

        arrow1 = Arrow(raw_group.get_right(), num_group.get_left(), buff=0.2, color=WHITE)
        arrow2 = Arrow(num_group.get_right(), model_group.get_left(), buff=0.2, color=WHITE)

        bottom_note = Text("You do not manually analyze each image. The computer does it with math.", font_size=24, color=WHITE)
        bottom_note.to_edge(DOWN)

        self.play(Write(title))
        self.play(FadeIn(subtitle, shift=DOWN))
        self.wait(0.5)

        self.play(FadeIn(raw_group, shift=RIGHT))
        self.play(GrowArrow(arrow1))
        self.play(FadeIn(num_group, shift=UP))
        self.play(GrowArrow(arrow2))
        self.play(FadeIn(model_group, shift=RIGHT))
        self.play(Write(bottom_note))
        self.wait(2)