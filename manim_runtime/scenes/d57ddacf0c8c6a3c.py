from manim import *

class GeneratedScene(Scene):
    def construct(self):
        self.camera.background_color = BLACK
        title = Text("Velocity", font_size=34, color=WHITE)
        title.scale_to_fit_width(10.8)
        title.to_edge(UP, buff=0.45)
        topic = Text("Velocity", font_size=24, color=YELLOW)
        topic.next_to(title, DOWN, buff=0.2)

        labels = ["Reveal the first idea", "Move the marker to the changing step", "Highlight the result", "Circle the whole pattern"]
        left = Circle(radius=0.42, color=BLUE)
        middle = Rectangle(width=1.55, height=0.82, color=GREEN)
        right = Circle(radius=0.42, color=ORANGE)
        flow = VGroup(left, middle, right).arrange(RIGHT, buff=1.05)
        flow.move_to(DOWN * 0.25)

        dot = Dot(left.get_center(), radius=0.08, color=YELLOW)
        arrow_one = Arrow(left.get_right(), middle.get_left(), buff=0.15, color=BLUE)
        arrow_two = Arrow(middle.get_right(), right.get_left(), buff=0.15, color=BLUE)
        caption = Text(labels[0], font_size=24, color=WHITE)
        caption.scale_to_fit_width(9.2)
        caption.to_edge(DOWN, buff=0.7)

        self.play(Write(title), FadeIn(topic, shift=DOWN * 0.08), run_time=0.7)
        self.play(Create(left), FadeIn(dot), Write(caption), run_time=0.8)
        self.play(Create(arrow_one), dot.animate.move_to(middle.get_center()), Transform(caption, Text(labels[min(1, len(labels)-1)], font_size=24, color=WHITE).scale_to_fit_width(9.2).to_edge(DOWN, buff=0.7)), run_time=1.2)
        self.play(Create(middle), Indicate(middle, color=YELLOW), run_time=0.7)
        self.play(Create(arrow_two), dot.animate.move_to(right.get_center()), Transform(caption, Text(labels[min(2, len(labels)-1)], font_size=24, color=WHITE).scale_to_fit_width(9.2).to_edge(DOWN, buff=0.7)), run_time=1.2)
        self.play(Create(right), Circumscribe(flow, color=YELLOW), run_time=0.8)
        if len(labels) > 3:
            final_caption = Text(labels[3], font_size=24, color=YELLOW)
            final_caption.scale_to_fit_width(9.2)
            final_caption.to_edge(DOWN, buff=0.7)
            self.play(Transform(caption, final_caption), run_time=0.5)
        self.wait(1.0)