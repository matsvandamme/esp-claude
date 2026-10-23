#include "button.h"
#include "display.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "led.h"
#include "link_usb.h"
#include "link_wifi.h"
#include "nvs_flash.h"
#include "proto.h"
#include "ui.h"

static void nvs_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

static void wifi_driver_init(void)
{
    // Started (not connected) from day one so heap figures reflect the real budget.
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
}

void app_main(void)
{
    nvs_init();
    proto_init();
    proto_set_handler(ui_on_message);
    link_usb_init();
    led_init();
    wifi_driver_init();
    link_wifi_init();
    display_init();
    ui_init();
    button_init(ui_on_button);
    display_run(proto_poll);
}
