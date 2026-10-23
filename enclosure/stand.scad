// Desk stand for the ESP32-C6-LCD-1.47 Claude Code display
// Bambu Lab A1 + AMS: black PLA (body) + yellow PLA (inlays). No supports needed.
//
// Parts (choose with `part`):
//   bezel        front shell: screen window, BOOT flexure button, USB-C opening   -> print face down
//   bezel_inlay  yellow frame around the screen (flush in the bezel face)          -> filament 2
//   back         snap-in back cover; posts pin into the board's four M2 nuts      -> print back down
//   base         15 degree cradle with a front ledge                               -> print flat
//   base_inlay   yellow pixel Clawd on the ledge                                   -> filament 2
//   assembly     everything on the desk, with the reference board if present
//
// Board frame (reference model by x_giedrius_x, Printables 1633740): origin at the board
// centre, z = 0 at the back of the PCB, +z towards the viewer, +x towards USB-C, -y is the
// BOOT edge. On the desk the board is turned 180 degrees: USB-C on the left, BOOT on top,
// which is how the firmware draws the screen upright.

/* [Part] */
part = "assembly"; // [assembly, exploded, bezel, bezel_inlay, back, base, base_inlay]
show_board = true; // Show the reference board in the assembly (needs ref/esp32_c6_lcd_147.stl)

/* [Fit (A1, 0.4 nozzle, PLA)] */
clear = 0.20;      // [0.1:0.05:0.4] Board to cavity, per side
snap = 0.35;       // [0.2:0.05:0.5] Snap bump height (more = tighter back cover)
base_clear = 0.25; // [0.15:0.05:0.5] Case to cradle, per side

/* [Shape] */
tilt = 15;         // [0:1:30] Screen lean, degrees
wall = 2.4;        // [2.0:0.2:3.2] Side walls (holds the BOOT flexure)
front = 1.2;       // [0.8:0.2:2.0] Front plate
lip = 0.8;         // [0.4:0.1:1.5] Bezel overlap on the glass edge
floor_h = 4.0;     // [2:0.5:8] Case above the desk
ledge = 13.0;      // [8:1:20] Front ledge depth (Clawd lives here)
lip_up = 2.6;      // [1.5:0.1:3.2] Cradle lip in front of the screen
back_up = 13.0;    // [8:1:20] Back support height
inlay_d = 0.6;     // [0.4:0.2:1.0] Yellow inlay depth (3 layers at 0.2)
inlay_w = 1.0;     // [0.6:0.2:2.0] Yellow frame width

/* [Hidden] */
$fn = 48;
eps = 0.01;

// ---- board, measured from the reference STL ----
board_l = 36.36; board_w = 20.32; board_r = 2.75;
glass_top = 5.6;              // LCD glass front above the PCB back
back_depth = 3.21;            // deepest part behind the PCB (USB-C shell)
nuts = [[-16.21, -6.64], [-16.21, 6.64], [15.79, -8.16], [15.79, 8.16]]; // SMT M2 nuts
nut_d = 3.5; nut_h = 2.8;
usb_y = 0; usb_z = -1.13;     // USB-C centre
btn_x = [9.03, 13.67];        // side switches, both long edges
btn_z = [-1.8, 0.06];
btn_out = 11.28;              // actuator tip from the centre line

// ---- derived ----
cav_l = board_l + 2 * clear; cav_w = board_w + 2 * clear; cav_r = board_r + clear;
out_l = cav_l + 2 * wall;    out_w = cav_w + 2 * wall;    out_r = cav_r + wall;
z_front_in = glass_top + 0.1;
z_front = z_front_in + front;
z_back_in = -(back_depth + 0.39);
back_t = 1.6;
z_back = z_back_in - back_t;
win_l = board_l - 2 * lip; win_w = board_w - 2 * lip; win_r = 1.6;

tab_x = [1.5, 14.6];          // BOOT flexure: hinge at low x, free end towards USB-C (straight wall)
tab_top = z_front - 4.6;      // top edge of the tab (2.3), on a layer boundary when printed face down
slot = 0.8;                   // gap above the tab: room for the bridge to sag without touching
tie = 0.2;                    // one printed layer: the tab's first (bridge) layer is tied to the
                              // wall at the free end, so it is not a 13 mm cantilever over air
tab_in = btn_out + 0.2;       // 0.2 mm from the actuator at rest
rim_h = 2.0;
snap_x = [-10.5, -2.0];       // on the rim (x -13.4..8.5), clear of the BOOT flexure (x 1.5..15.2)
snap_z = z_back_in + 1.1;     // leaves a 0.6 mm catch below the groove

// ---- helpers ----
module box(x0, x1, y0, y1, z0, z1) translate([x0, y0, z0]) cube([x1 - x0, y1 - y0, z1 - z0]);

module rrect(l, w, r, z0, z1)
    translate([0, 0, z0]) linear_extrude(z1 - z0)
        offset(r = r, $fn = 64) square([l - 2 * r, w - 2 * r], center = true);

// Board frame -> desk (X right, Y away from you, Z up).
function ymin_desk() = -z_front * cos(tilt) - out_w / 2 * sin(tilt);
function zmin_desk() = z_back * sin(tilt) - out_w / 2 * cos(tilt);
module on_desk()
    translate([0, ledge - ymin_desk(), floor_h - zmin_desk()])
        rotate([-tilt, 0, 0]) rotate([90, 0, 0]) rotate([0, 0, 180]) children();

// ---- parts ----
module inlay_ring()
    difference() {
        rrect(win_l + 2 * inlay_w, win_w + 2 * inlay_w, win_r + inlay_w, z_front - inlay_d, z_front);
        rrect(win_l, win_w, win_r, z_front - inlay_d - 1, z_front + 1);
    }

module bezel() {
    difference() {
        union() {
            rrect(out_l, out_w, out_r, z_back_in, z_front);
            // Dome on the BOOT tab: find the button by touch.
            translate([(btn_x[0] + btn_x[1]) / 2, -out_w / 2, (btn_z[0] + btn_z[1]) / 2])
                rotate([90, 0, 0]) cylinder(h = 0.8, r = 1.4, center = true);
        }
        rrect(cav_l, cav_w, cav_r, z_back_in - 1, z_front_in);
        rrect(win_l, win_w, win_r, z_front_in - 1, z_front + 1);
        inlay_ring();
        // Paths for both side switches: the board slides in from the back.
        box(btn_x[0] - 0.3, btn_x[1] + 0.3, cav_w / 2 - eps, tab_in, z_back_in - 1, btn_z[1] + 0.3);
        box(btn_x[0] - 0.3, btn_x[1] + 0.3, -tab_in, -cav_w / 2 + eps, z_back_in - 1, btn_z[1] + 0.3);
        // BOOT flexure: free the tab on two sides (top, free end); it is open at the back.
        // Printed face down, the tab's top edge is its lowest layer: a bridge over the straight
        // top slot from the hinge to a one-layer tie at the free end (a slot cut right through
        // would leave a 13 mm cantilever over air). One firm press on the dome breaks the tie.
        box(tab_x[0], tab_x[1] + slot, -out_w, -tab_in + 0.1, tab_top, tab_top + slot);
        box(tab_x[1], tab_x[1] + slot, -out_w, -cav_w / 2 + eps, z_back_in - 1, tab_top - tie);
        // Thin the tab from the inside, up to the slot, so the tab never touches the LCD edge
        // when pressed. Its top is a floor when printing face down.
        box(tab_x[0], tab_x[1] + slot, -tab_in, -cav_w / 2 + eps, z_back_in - 1, tab_top + slot);
        // USB-C: room for a typical cable overmould (12.5 x 7.5), open towards the back.
        box(cav_l / 2 - 0.5, out_l, usb_y - 6.25, usb_y + 6.25, z_back_in - 1, usb_z + 3.75);
        // Snap grooves for the back cover.
        for (x = snap_x, s = [-1, 1])
            box(x - 2.3, x + 2.3, min(s * (cav_w / 2 - eps), s * (cav_w / 2 + snap + 0.1)),
                max(s * (cav_w / 2 - eps), s * (cav_w / 2 + snap + 0.1)), snap_z - 0.5, snap_z + 0.5);
    }
}

module back() {
    rim_l = cav_l - 0.3; rim_w = cav_w - 0.3; rim_r = cav_r - 0.15;
    difference() {
        union() {
            rrect(out_l, out_w, out_r, z_back, z_back_in);
            difference() {
                rrect(rim_l, rim_w, rim_r, z_back_in - eps, z_back_in + rim_h);
                rrect(rim_l - 2.4, rim_w - 2.4, max(rim_r - 1.2, 0.5), z_back_in - 1, z_back_in + rim_h + 1);
                box(8.5, out_l, -out_w, out_w, z_back_in - 1, z_back_in + rim_h + 1); // switches, USB-C, nuts
                box(-out_l, -13.4, -out_w, out_w, z_back_in - 1, z_back_in + rim_h + 1); // nut end
            }
            for (x = snap_x, s = [-1, 1])
                translate([x, s * rim_w / 2, snap_z]) rotate([0, 90, 0]) cylinder(h = 4, r = snap / 2 + 0.25, center = true);
            // Posts onto the M2 nuts, with locating pins.
            for (n = nuts) translate([n[0], n[1], z_back_in - eps]) {
                cylinder(h = -nut_h - 0.05 - z_back_in + eps, d = nut_d);
                cylinder(h = -nut_h - 0.05 - z_back_in + 1.5, d = 1.4);
            }
        }
        box(cav_l / 2 - 0.5, out_l + 1, usb_y - 6.25, usb_y + 6.25, z_back - 1, z_back_in + eps); // through
    }
}

// Clawd's idle pose (firmware/main/clawd.c): 18 x 5 grid, eyes as holes.
clawd_rows = [
    "...############...",
    "...##.######.##...",
    ".################.",
    "...############...",
    "....#.#....#.#....",
];
pix_x = 0.75; pix_y = 1.3;
lip_top = floor_h + lip_up;

module clawd() {
    x0 = -out_l / 2 + 4.0;
    y0 = (ledge - len(clawd_rows) * pix_y) / 2;
    for (r = [0 : len(clawd_rows) - 1], c = [0 : len(clawd_rows[0]) - 1])
        if (clawd_rows[r][c] == "#")
            box(x0 + c * pix_x - eps, x0 + (c + 1) * pix_x + eps,
                y0 + (len(clawd_rows) - 1 - r) * pix_y - eps, y0 + (len(clawd_rows) - r) * pix_y + eps,
                lip_top - inlay_d, lip_top);
}

function y_back_desk() = -z_back * cos(tilt) + out_w / 2 * sin(tilt) + (ledge - ymin_desk());

module base() {
    difference() {
        box(-out_l / 2, out_l / 2, 0, y_back_desk() + 2.4, 0, floor_h + back_up);
        // The case drops in from above.
        on_desk() box(-out_l, out_l, -out_w / 2 - 80, out_w / 2 + base_clear, z_back - base_clear, z_front + base_clear);
        // Nothing in front of the screen above the lip.
        intersection() {
            // Starts inside the slot: if the two cuts merely touched, a zero-thickness sheet
            // would stay standing in front of the screen.
            on_desk() box(-out_l, out_l, -out_w - 80, out_w + 80, z_front, z_front + 80);
            box(-out_l, out_l, -50, 200, lip_top, 100);
        }
        clawd();
    }
}

// ---- output ----
if (part == "bezel") bezel();
else if (part == "bezel_inlay") inlay_ring();
else if (part == "back") back();
else if (part == "base") base();
else if (part == "base_inlay") clawd();
else if (part == "exploded") {
    // Board frame, pulled apart along the screen normal; base below.
    translate([0, 0, 22]) { color("#2a2a2a") bezel(); color("#ffd200") inlay_ring(); }
    if (show_board) translate([0, 0, 8]) color("#3060a0") import("ref/esp32_c6_lcd_147.stl");
    translate([0, 0, -6]) color("#333333") back();
}
else if (part == "assembly") {
    color("#202020") base();
    color("#ffd200") clawd();
    on_desk() {
        color("#2a2a2a") bezel();
        color("#ffd200") inlay_ring();
        color("#333333") back();
        if (show_board) color("#3060a0", 0.9) import("ref/esp32_c6_lcd_147.stl");
    }
}
