#include <windows.h>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <random>
#include <chrono>
#include "interception.h"

// Exported C API for Python interop
extern "C" {

__declspec(dllexport) void init();
__declspec(dllexport) void set_device(int id);
__declspec(dllexport) void move_abs(int x, int y);
__declspec(dllexport) void driver_click(int duration_ms);
__declspec(dllexport) int detect_mouse_click();

}

// Global Interception context and device id
static InterceptionContext g_context = nullptr;
static InterceptionDevice g_device = 0;

// RNG for randomized click delay
static std::mt19937 g_rng;
static bool g_rng_seeded = false;

static void ensure_rng_seeded()
{
    if (g_rng_seeded)
        return;
    // Try to seed with non-deterministic random device, fallback to time
    try {
        std::random_device rd;
        std::seed_seq seq{rd(), rd(), rd(), rd(), rd(), rd()};
        g_rng.seed(seq);
    }
    catch (...) {
        g_rng.seed(static_cast<unsigned int>(
            std::chrono::high_resolution_clock::now().time_since_epoch().count()));
    }
    g_rng_seeded = true;
}

// Create or reuse the interception context
extern "C" __declspec(dllexport) void init()
{
    if (g_context != nullptr) {
        return;
    }
    g_context = interception_create_context();
    ensure_rng_seeded();
}

extern "C" __declspec(dllexport) void set_device(int id)
{
    g_device = static_cast<InterceptionDevice>(id);
}

// Helper: clamp to [0,65535]
static inline int clamp_abs(long long v)
{
    if (v < 0) return 0;
    if (v > 0xFFFF) return 0xFFFF;
    return static_cast<int>(v);
}

extern "C" __declspec(dllexport) void move_abs(int x, int y)
{
    if (g_context == nullptr) {
        // Lazily initialize context if not already
        init();
        if (g_context == nullptr) {
            return;
        }
    }

    // Get screen dimensions using GetSystemMetrics as requested
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int screenH = GetSystemMetrics(SM_CYSCREEN);

    // Defensive fallbacks
    if (screenW <= 1) screenW = 1;
    if (screenH <= 1) screenH = 1;

    // Map pixel coords to Interception absolute range [0,65535]
    // Use double arithmetic for accuracy and map full range inclusive
    double fx = static_cast<double>(x);
    double fy = static_cast<double>(y);
    double maxX = static_cast<double>(screenW - 1);
    double maxY = static_cast<double>(screenH - 1);

    long long absX = 0;
    long long absY = 0;

    if (maxX > 0.0) {
        absX = static_cast<long long>(std::llround((fx * 65535.0) / maxX));
    }
    if (maxY > 0.0) {
        absY = static_cast<long long>(std::llround((fy * 65535.0) / maxY));
    }

    InterceptionMouseStroke stroke;
    // Important: zero the stroke structure to avoid garbage data injection
    std::memset(&stroke, 0, sizeof(stroke));

    stroke.flags = INTERCEPTION_MOUSE_MOVE_ABSOLUTE;
    // Optionally include virtual desktop mapping if desired by caller
    // stroke.flags |= INTERCEPTION_MOUSE_VIRTUAL_DESKTOP;

    stroke.x = clamp_abs(absX);
    stroke.y = clamp_abs(absY);

    // state remains 0 for pure movement
    stroke.state = 0;

    // Send the stroke
    interception_send(g_context, g_device, reinterpret_cast<const InterceptionStroke*>(&stroke), 1);
}

// Simple helper to return randomized delay in milliseconds between 40 and 70
static inline int random_delay_ms()
{
    ensure_rng_seeded();
    std::uniform_int_distribution<int> dist(40, 70);
    return dist(g_rng);
}

extern "C" __declspec(dllexport) void driver_click(int duration_ms)
{
    if (g_context == nullptr) {
        init();
        if (g_context == nullptr) {
            return;
        }
    }

    InterceptionMouseStroke stroke;
    // Zero before setting fields to avoid leftover garbage
    std::memset(&stroke, 0, sizeof(stroke));

    // LEFT BUTTON DOWN
    stroke.state = INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN;
    interception_send(g_context, g_device, reinterpret_cast<const InterceptionStroke*>(&stroke), 1);

    // Sleep for the duration passed from Python
    Sleep(static_cast<DWORD>(duration_ms));

    // LEFT BUTTON UP - zero the struct again to avoid flag conflicts
    std::memset(&stroke, 0, sizeof(stroke));
    stroke.state = INTERCEPTION_MOUSE_LEFT_BUTTON_UP;
    interception_send(g_context, g_device, reinterpret_cast<const InterceptionStroke*>(&stroke), 1);
}

// Detect mouse click from user input
extern "C" __declspec(dllexport) int detect_mouse_click()
{
    // Create a new context dedicated to input detection
    InterceptionContext detect_context = interception_create_context();
    if (detect_context == nullptr) {
        return -1; // Error: could not create context
    }

    // Set filter to listen only for left mouse button down events on all devices
    interception_set_filter(detect_context, interception_is_mouse, 
                            INTERCEPTION_FILTER_MOUSE_LEFT_BUTTON_DOWN);

    InterceptionDevice detected_device = -1;

    // Wait for mouse input
    while (true) {
        InterceptionDevice device = interception_wait(detect_context);

        if (interception_is_invalid(device)) {
            break; // Invalid device, stop waiting
        }

        // Receive the stroke event
        InterceptionMouseStroke stroke;
        std::memset(&stroke, 0, sizeof(stroke));

        int received = interception_receive(detect_context, device, 
                                           reinterpret_cast<InterceptionStroke*>(&stroke), 1);

        if (received > 0) {
            // Check if this is a left button down event
            if ((stroke.state & INTERCEPTION_MOUSE_LEFT_BUTTON_DOWN) != 0) {
                detected_device = device;

                // Forward the event so the click actually happens on the PC
                interception_send(detect_context, device, 
                                 reinterpret_cast<const InterceptionStroke*>(&stroke), 1);

                // We found the mouse click, exit the loop
                break;
            }

            // Forward any other events that weren't the left click
            interception_send(detect_context, device, 
                             reinterpret_cast<const InterceptionStroke*>(&stroke), 1);
        }
    }

    // Clean up the detection context
    interception_destroy_context(detect_context);

    // Return the device ID of the mouse that was clicked
    return detected_device;
}
