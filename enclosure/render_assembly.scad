// Pictures of the stand from the exported meshes (out/*.stl): what gets printed, nothing else.
// view = "desk" (assembled) or "exploded" (board frame, pulled apart along the screen normal).
// The inlays are lifted by `lift` so their flush faces don't z-fight with the part around them.
// paint = "mask" / "corners" are helper passes for make_cover.py (same camera, fixed with --camera):
//   mask     everything black, the LCD's active area green: where the screen picture is visible
//   corners  only four coloured dots at the active area's corners: where the picture's corners go
include <stand.scad>
part = "none";
view = "desk"; // [desk, exploded]
paint = "normal"; // [normal, mask, corners]
lift = 0.05;
screen = true; // plain dark block where the board sits (our own shape, not the reference model)
active = [32.35, 17.39]; // ST7789 1.47" 172x320 active area, assumed centred on the board

function c(normal) = paint == "normal" ? normal : "#000000";

module stand_in() if (screen && !show_board) color(c("#0b1626")) rrect(board_l, board_w, board_r, 0, glass_top);

module active_area() {
    if (paint == "mask")
        color("#00ff00") translate([-active[0] / 2, -active[1] / 2, glass_top]) cube([active[0], active[1], 0.02]);
    if (paint == "corners")
        for (i = [0 : 3]) {
            sx = i == 0 || i == 3 ? -1 : 1;
            sy = i < 2 ? -1 : 1;
            color(["#ff0000", "#00ff00", "#0000ff", "#ffff00"][i])
                translate([sx * active[0] / 2 - 0.25, sy * active[1] / 2 - 0.25, glass_top]) cube([0.5, 0.5, 0.02]);
        }
}

module bezel_set() {
    color(c("#262626")) import("out/bezel.stl");
    color(c("#f4d21a")) translate([0, 0, lift]) import("out/bezel_inlay.stl");
}

if (paint == "corners") {
    on_desk() active_area();
} else if (view == "exploded") {
    translate([0, 0, 22]) bezel_set();
    translate([0, 0, 8]) stand_in();
    if (show_board) translate([0, 0, 8]) color("#2b4f86") import("ref/esp32_c6_lcd_147.stl");
    translate([0, 0, -6]) color(c("#303030")) import("out/back.stl");
} else {
    color(c("#1c1c1c")) import("out/base.stl");
    color(c("#f4d21a")) translate([0, 0, lift]) import("out/base_inlay.stl");
    on_desk() {
        bezel_set();
        color(c("#303030")) import("out/back.stl");
        stand_in();
        active_area();
        if (show_board) color("#2b4f86") import("ref/esp32_c6_lcd_147.stl");
    }
}
