idf_component_register(
    SRCS "app_main.c" "button.c" "clawd.c" "display.c" "led.c" "link_usb.c" "link_wifi.c" "ota.c" "proto.c" "shot.c" "snap.c"
         "stats.c" "ui.c"
         "fonts/font_mono_11.c" "fonts/font_mono_13.c" "fonts/font_mono_16b.c" "fonts/font_mono_20b.c" "fonts/font_clock_44b.c"
    INCLUDE_DIRS "."
    REQUIRES esp_lcd esp_driver_ledc esp_driver_spi esp_driver_gpio esp_driver_usb_serial_jtag esp_timer
             nvs_flash app_update esp_wifi esp_netif esp_event json mbedtls lwip espressif__mdns
)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -Wno-format-truncation)
