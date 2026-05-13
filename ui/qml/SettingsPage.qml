import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    property var captchaModelNames: []

    ScrollView {
        anchors.fill: parent
        anchors.margins: 10
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme.spacingMedium

            Card {
                title: "Config Context"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    ActionButton {
                        text: "Client 1"
                        baseColor: backend.activeConfigClient === 0 ? Theme.accent : Theme.bgTertiary
                        hoverColor: backend.activeConfigClient === 0 ? Theme.accentHover : Theme.borderLight
                        Layout.fillWidth: true
                        implicitHeight: 32
                        onClicked: backend.activeConfigClient = 0
                    }

                    ActionButton {
                        text: "Client 2"
                        baseColor: backend.activeConfigClient === 1 ? Theme.accent : Theme.bgTertiary
                        hoverColor: backend.activeConfigClient === 1 ? Theme.accentHover : Theme.borderLight
                        Layout.fillWidth: true
                        implicitHeight: 32
                        onClicked: backend.activeConfigClient = 1
                    }
                }
            }

            // ─── Combat & Vision ───
            Card {
                title: "Combat & Vision"
                Layout.fillWidth: true

                SliderOption {
                    text: "Detection Confidence"
                    from: 0.1; to: 1.0; stepSize: 0.05
                    value: backend.yoloConfidence
                    onValueEdited: backend.yoloConfidence = value
                }
                ActionButton {
                    text: "Mask UI Regions (" + backend.maskRegionCount + ")"
                    baseColor: Theme.bgTertiary
                    hoverColor: Theme.borderLight
                    Layout.fillWidth: true
                    implicitHeight: 32
                    onClicked: backend.startMaskRegionSelection()
                }
                ComboOption {
                    text: "Target Selection"
                    modelData: ["Nearest", "Random", "Largest"]
                    currentIndex: backend.selectionModeIndex
                    onSelectionChanged: backend.selectionModeIndex = currentIndex
                }
                SpinOption {
                    text: "Combat Timeout"
                    from: 10; to: 300; value: backend.combatTimeout
                    suffix: "s"
                    onValueChanged: backend.combatTimeout = value
                }
                SpinOption {
                    text: "Scan Radius"
                    from: 100; to: 2000; value: backend.scanRadius
                    suffix: "px"
                    onValueChanged: backend.scanRadius = value
                }
                ToggleOption {
                    text: "Enable Multi-Target Queue"
                    checked: backend.multiTargetQueueEnabled
                    onToggled: backend.multiTargetQueueEnabled = checked
                }
                SpinOption {
                    text: "Queue Size"
                    from: 1; to: 999; value: backend.multiTargetQueueSize
                    enabled: backend.multiTargetQueueEnabled
                    opacity: enabled ? 1.0 : 0.55
                    onValueChanged: backend.multiTargetQueueSize = value
                }
            }

            // ─── Timings ───
            Card {
                title: "Timings"
                Layout.fillWidth: true

                SpinOption {
                    text: "Revive Delay"
                    from: 1; to: 30; value: backend.reviveDelay
                    suffix: "s"
                    onValueChanged: backend.reviveDelay = value
                }
                SpinOption {
                    text: "Skill Check Interval"
                    from: 1; to: 120; value: backend.skillCheckInterval
                    suffix: "s"
                    onValueChanged: backend.skillCheckInterval = value
                }
                SpinOption {
                    text: "Quest Check Interval"
                    from: 1; to: 60; value: backend.questCheckInterval
                    suffix: "s"
                    onValueChanged: backend.questCheckInterval = value
                }
            }

            // ─── Advanced Combat ───
            Card {
                title: "Advanced Combat (Expert)"
                Layout.fillWidth: true

                SpinOption {
                    text: "Miss Timeout"
                    from: 1; to: 10; value: backend.missTimeout
                    suffix: "s"
                    onValueChanged: backend.missTimeout = value
                }
                SpinOption {
                    text: "Movement Timeout"
                    from: 3; to: 30; value: backend.movementTimeout
                    suffix: "s"
                    onValueChanged: backend.movementTimeout = value
                }
                SpinOption {
                    text: "Verify Timeout"
                    from: 1; to: 10; value: backend.verifyTimeout
                    suffix: "s"
                    onValueChanged: backend.verifyTimeout = value
                }
                SpinOption {
                    text: "Strafe Start Delay"
                    from: 1; to: 10; value: backend.strafeStartDelay
                    suffix: "s"
                    onValueChanged: backend.strafeStartDelay = value
                }
                SpinOption {
                    text: "Strafe Interval"
                    from: 1; to: 5; value: backend.strafeInterval
                    suffix: "s"
                    onValueChanged: backend.strafeInterval = value
                }
            }

            // ─── Feature Toggles ───
            Card {
                title: "Features"
                Layout.fillWidth: true

                ToggleOption { text: "Auto Loot"; checked: backend.autoLoot; onToggled: backend.autoLoot = checked }
                ToggleOption { text: "Auto Skills"; checked: backend.autoSkills; onToggled: backend.autoSkills = checked }
                ToggleOption { text: "Auto Revive"; checked: backend.autoRevive; onToggled: backend.autoRevive = checked }
                ToggleOption { text: "Anti-Stuck System"; checked: backend.antiStuck; onToggled: backend.antiStuck = checked }
                ToggleOption { text: "Captcha Solver"; checked: backend.captchaSolver; onToggled: backend.captchaSolver = checked }
                ToggleOption { text: "Auto Quest Book"; checked: backend.questEnabled; onToggled: backend.questEnabled = checked }
                ToggleOption { text: "Debug Mode"; checked: backend.debugMode; onToggled: backend.debugMode = checked }
            }

            // ─── AI CAPTCHA Solver ───
            Card {
                title: "AI CAPTCHA Solver"
                Layout.fillWidth: true

                ToggleOption {
                    text: "Enable Global CAPTCHA Solver"
                    checked: backend.captchaEnabled
                    onToggled: backend.captchaEnabled = checked
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "API Key"
                        color: Theme.textPrimary
                        font { pixelSize: 10; family: "Segoe UI" }
                        Layout.preferredWidth: 90
                    }

                    TextField {
                        id: captchaApiKeyField
                        Layout.fillWidth: true
                        echoMode: TextInput.Password
                        placeholderText: "AIza..."
                        text: backend.captchaApiKey
                        color: Theme.textPrimary
                        font { pixelSize: 10; family: "Segoe UI" }
                        background: Rectangle {
                            color: Theme.bgPrimary
                            radius: Theme.radiusSmall
                            border.color: Theme.border
                            border.width: 1
                        }
                        onEditingFinished: backend.captchaApiKey = text
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Model"
                        color: Theme.textPrimary
                        font { pixelSize: 10; family: "Segoe UI" }
                        Layout.preferredWidth: 90
                    }

                    ComboBox {
                        id: captchaModelCombo
                        Layout.fillWidth: true
                        model: captchaModelNames
                        enabled: captchaModelNames.length > 0
                        font { pixelSize: 10; family: "Segoe UI" }
                        onActivated: {
                            if (currentIndex >= 0 && currentIndex < captchaModelNames.length)
                                backend.captchaSelectedModel = captchaModelNames[currentIndex]
                        }
                    }

                    ActionButton {
                        text: "Fetch Models"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitHeight: 30
                        Layout.preferredWidth: 110
                        onClicked: {
                            var fetched = backend.fetch_gemini_models(captchaApiKeyField.text)
                            if (fetched && fetched.length > 0) {
                                captchaModelNames = fetched
                                var selected = backend.captchaSelectedModel
                                var idx = fetched.indexOf(selected)
                                captchaModelCombo.currentIndex = idx >= 0 ? idx : 0
                                if (captchaModelCombo.currentIndex >= 0)
                                    backend.captchaSelectedModel = fetched[captchaModelCombo.currentIndex]
                            }
                        }
                    }
                }
            }

            // ─── System ───
            Card {
                title: "System"
                Layout.fillWidth: true

                SpinOption {
                    text: "Mouse Device ID"
                    from: 0; to: 100; value: backend.mouseId
                    onValueChanged: backend.mouseId = value
                }
                ToggleOption { text: "Always On Top"; checked: backend.alwaysOnTop; onToggled: backend.alwaysOnTop = checked }
            }

            // ─── Save ───
            ActionButton {
                text: "Save Settings"
                baseColor: Theme.accent
                hoverColor: Theme.accentHover
                Layout.fillWidth: true
                implicitHeight: 38
                onClicked: backend.saveConfig()
            }

            Item { height: 10 }
        }
    }

    Component.onCompleted: {
        var defaultModels = [backend.captchaSelectedModel || "gemini-2.5-flash"]
        captchaModelNames = defaultModels
        captchaModelCombo.currentIndex = 0
    }
}
