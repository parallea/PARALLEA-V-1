from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Why ML needs math", font_size=40, weight=BOLD)
        subtitle = Text("Image -> numbers -> pattern detection -> score -> label", font_size=28)
        subtitle.next_to(title, DOWN, buff=0.35)

        self.play(Write(title))
        self.play(FadeIn(subtitle, shift=DOWN))
        self.wait(0.5)

        # Left: raw image icon
        image_box = Rectangle(width=2.4, height=2.4, color=BLUE)
        image_label = Text("Raw image", font_size=24).next_to(image_box, UP, buff=0.2)
        simple_animal = VGroup(
            Circle(radius=0.45, color=WHITE),
            Dot(point=LEFT*0.18 + UP*0.15, radius=0.05, color=WHITE),
            Dot(point=RIGHT*0.18 + UP*0.15, radius=0.05, color=WHITE),
            Line(LEFT*0.12 + DOWN*0.05, RIGHT*0.12 + DOWN*0.05, color=WHITE),
            Line(LEFT*0.55 + UP*0.55, LEFT*0.8 + UP*0.85, color=WHITE),
            Line(RIGHT*0.55 + UP*0.55, RIGHT*0.8 + UP*0.85, color=WHITE),
        )
        simple_animal.scale(0.8)
        simple_animal.move_to(image_box.get_center())
        raw_group = VGroup(image_box, image_label, simple_animal)
        raw_group.shift(LEFT*4.2 + DOWN*0.3)

        # Middle: numbers/grid
        grid = VGroup()
        cell_size = 0.32
        for r in range(5):
            for c in range(5):
                cell = Rectangle(width=cell_size, height=cell_size, stroke_width=1, color=GREY_B)
                x = (c - 2) * cell_size
                y = (2 - r) * cell_size
                cell.move_to([x, y, 0])
                if (r + c) % 2 == 0:
                    cell.set_fill(BLUE_E, opacity=0.65)
                else:
                    cell.set_fill(BLUE_D, opacity=0.35)
                grid.add(cell)
        grid_box = SurroundingRectangle(grid, color=BLUE)
        numbers_label = Text("Pixels become numbers", font_size=24).next_to(grid_box, UP, buff=0.2)
        grid_group = VGroup(grid, grid_box, numbers_label).move_to(ORIGIN)

        # Right: model and output
        model_box = Rectangle(width=2.9, height=2.0, color=GREEN)
        model_text = Text("Model", font_size=28, weight=BOLD).move_to(model_box.get_center() + UP*0.35)
        pattern_text = Text("finds patterns", font_size=24).move_to(model_box.get_center() + DOWN*0.2)
        output_box = Rectangle(width=2.5, height=1.0, color=YELLOW)
        output_text = Text("Dog  /  Cat", font_size=28, weight=BOLD).move_to(output_box.get_center())
        model_group = VGroup(model_box, model_text, pattern_text, output_box, output_text)
        model_group.shift(RIGHT*4.1 + DOWN*0.1)
        output_box.next_to(model_box, DOWN, buff=0.35)
        output_text.move_to(output_box.get_center())

        # Arrows
        arrow1 = Arrow(raw_group.get_right(), grid_group.get_left(), buff=0.2, color=WHITE)
        arrow2 = Arrow(grid_group.get_right(), model_group.get_left(), buff=0.2, color=WHITE)
        arrow3 = Arrow(model_box.get_bottom(), output_box.get_top(), buff=0.1, color=WHITE)

        # Teach in steps
        step1 = Text("1) Image -> numbers", font_size=26)
        step2 = Text("2) Numbers -> pattern detection", font_size=26)
        step3 = Text("3) Higher score wins", font_size=26)
        step_group = VGroup(step1, step2, step3).arrange(DOWN, aligned_edge=LEFT, buff=0.18)
        step_group.to_edge(DOWN, buff=0.4)

        self.play(FadeIn(raw_group, shift=LEFT), GrowArrow(arrow1))
        self.wait(0.6)
        self.play(FadeIn(grid_group, shift=UP), GrowArrow(arrow2))
        self.wait(0.6)
        self.play(FadeIn(model_group, shift=RIGHT), GrowArrow(arrow3))
        self.wait(0.6)

        highlight = SurroundingRectangle(model_box, color=GREEN, buff=0.15)
        score_text = Text("Dog score: 0.92   Cat score: 0.08", font_size=24)
        score_text.next_to(output_box, DOWN, buff=0.35)
        final_note = Text("Math helps compare patterns and choose the higher score.", font_size=24)
        final_note.to_edge(DOWN, buff=0.4)

        self.play(Write(step1))
        self.wait(0.4)
        self.play(Write(step2))
        self.wait(0.4)
        self.play(Write(step3))
        self.wait(0.4)
        self.play(Create(highlight))
        self.play(Write(score_text))
        self.play(Transform(step_group, final_note))
        self.wait(1.5)