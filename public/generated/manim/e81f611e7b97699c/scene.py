from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Why ML needs math", font_size=40, color=WHITE).to_edge(UP)
        subtitle = Text("Raw data -> numbers -> model prediction", font_size=26, color=GRAY).next_to(title, DOWN)
        self.play(Write(title), FadeIn(subtitle, shift=DOWN))
        self.wait(0.5)

        # Left: raw data box
        raw_box = RoundedRectangle(corner_radius=0.2, width=3.2, height=2.6, color=BLUE)
        raw_box.to_edge(LEFT, buff=0.7).shift(DOWN * 0.3)
        raw_label = Text("Raw data", font_size=28, color=WHITE).next_to(raw_box, UP, buff=0.2)

        img_icon = RoundedRectangle(corner_radius=0.12, width=0.7, height=0.7, color=GREEN)
        text_icon = RoundedRectangle(corner_radius=0.12, width=0.7, height=0.7, color=YELLOW)
        audio_icon = RoundedRectangle(corner_radius=0.12, width=0.7, height=0.7, color=ORANGE)
        img_icon.move_to(raw_box.get_left() + RIGHT * 0.7 + UP * 0.6)
        text_icon.move_to(raw_box.get_center() + LEFT * 0.25 + DOWN * 0.05)
        audio_icon.move_to(raw_box.get_left() + RIGHT * 1.7 + DOWN * 0.65)

        img_txt = Text("image", font_size=22, color=WHITE).move_to(img_icon)
        text_txt = Text("text", font_size=22, color=WHITE).move_to(text_icon)
        audio_txt = Text("audio", font_size=22, color=WHITE).move_to(audio_icon)

        # Middle: numbers box
        num_box = RoundedRectangle(corner_radius=0.2, width=3.2, height=2.6, color=GREEN)
        num_box.move_to([0, -0.3, 0])
        num_label = Text("Numbers", font_size=28, color=WHITE).next_to(num_box, UP, buff=0.2)
        num_lines = VGroup(
            Text("1  0  1  1", font_size=30, color=WHITE),
            Text("0.2 0.7 0.1", font_size=30, color=WHITE),
            Text("32  18  255", font_size=30, color=WHITE),
        ).arrange(DOWN, buff=0.18).move_to(num_box.get_center())

        # Right: model box
        model_box = RoundedRectangle(corner_radius=0.2, width=3.2, height=2.6, color=PURPLE)
        model_box.to_edge(RIGHT, buff=0.7).shift(DOWN * 0.3)
        model_label = Text("Model", font_size=28, color=WHITE).next_to(model_box, UP, buff=0.2)
        pred = Text("dog", font_size=34, color=YELLOW)
        pred.move_to(model_box.get_center())
        pred_caption = Text("prediction", font_size=22, color=GRAY).next_to(pred, DOWN, buff=0.15)

        # Arrows
        arrow1 = Arrow(raw_box.get_right(), num_box.get_left(), buff=0.15, color=WHITE)
        arrow2 = Arrow(num_box.get_right(), model_box.get_left(), buff=0.15, color=WHITE)

        # Bottom highlight
        math_strip = Rectangle(width=12.5, height=0.95, fill_color=BLACK, fill_opacity=0.35, stroke_width=0)
        math_strip.to_edge(DOWN, buff=0.15)
        math_text = Text("Linear algebra is the bridge that lets the model compute on numbers efficiently.", font_size=24, color=WHITE).move_to(math_strip)

        # Animate raw data appearing
        self.play(FadeIn(raw_box), Write(raw_label))
        self.play(FadeIn(img_icon), FadeIn(text_icon), FadeIn(audio_icon), Write(img_txt), Write(text_txt), Write(audio_txt))
        self.wait(0.5)

        # Convert to numbers
        self.play(FadeIn(num_box), Write(num_label))
        self.play(TransformFromCopy(img_icon, num_lines[0]), TransformFromCopy(text_icon, num_lines[1]), TransformFromCopy(audio_icon, num_lines[2]))
        self.play(Create(arrow1))
        self.wait(0.5)

        # Model prediction
        self.play(FadeIn(model_box), Write(model_label))
        self.play(Create(arrow2))
        self.play(Write(pred), FadeIn(pred_caption, shift=UP * 0.2))
        self.wait(0.5)

        # Emphasize the bridge
        self.play(FadeIn(math_strip))
        self.play(Write(math_text))
        self.play(Indicate(num_box, color=GREEN), Indicate(model_box, color=PURPLE))
        self.wait(2)