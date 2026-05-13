import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    id: globalTab
    property bool pageActive: false
    property var captchaModelNames: []
    property real globalConfidence: 0.45
    property bool syncingConfidence: false

    function syncSharedConfidence() {
        const previousClient = backend.activeConfigClient
        syncingConfidence = true
        backend.activeConfigClient = 0
        globalConfidence = backend.yoloConfidence
        backend.activeConfigClient = previousClient
        syncingConfidence = false
    }

    function applyConfidenceToAll(newValue) {
        if (syncingConfidence)
            return

        const previousClient = backend.activeConfigClient
        backend.activeConfigClient = 0
        backend.yoloConfidence = newValue
        backend.activeConfigClient = 1
        backend.yoloConfidence = newValue
        backend.activeConfigClient = previousClient
    }

    function ensureCaptchaModels() {
        const selected = backend.captchaSelectedModel
        if (!selected || selected.length === 0) {
            captchaModelNames = ["gemini-2.5-flash"]
            return
        }
        captchaModelNames = [selected]
    }

    function syncCaptchaSelection() {
        const selected = backend.captchaSelectedModel
        const idx = captchaModelNames.indexOf(selected)
        if (idx >= 0) {
            modelCombo.currentIndex = idx
        } else if (captchaModelNames.length > 0) {
            modelCombo.currentIndex = 0
            backend.captchaSelectedModel = captchaModelNames[0]
        }
    }

    onPageActiveChanged: {
        if (!pageActive)
            return
        syncSharedConfidence()
        syncCaptchaSelection()
    }

    Component.onCompleted: {
        ensureCaptchaModels()
        syncSharedConfidence()
        syncCaptchaSelection()
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme.spacingLarge

            Card {
                title: "Window Attachment"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Client 1"
                        color: Theme.textMuted
                        font.pixelSize: 11
                        Layout.preferredWidth: 64
                    }

                    ComboBox {
                        id: slot1Combo
                        Layout.fillWidth: true
                        model: backend.windowList
                        displayText: count > 0 ? currentText : "Select window..."

                        contentItem: Text {
                            text: slot1Combo.displayText
                            color: Theme.textPrimary
                            font.pixelSize: 11
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: 10
                            rightPadding: 30
                            elide: Text.ElideRight
                        }

                        background: Rectangle {
                            color: Theme.inputBackground
                            radius: Theme.radiusSmall
                            border.width: 1
                            border.color: slot1Combo.hovered ? Theme.borderLight : Theme.border
                        }
                    }

                    ActionButton {
                        text: backend.slot1Attached ? "Attached" : "Attach"
                        baseColor: backend.slot1Attached ? Theme.success : Theme.accent
                        hoverColor: backend.slot1Attached ? Theme.successHover : Theme.accentHover
                        Layout.preferredWidth: 90
                        implicitHeight: 34
                        enabled: slot1Combo.count > 0
                        onClicked: backend.attachWindowToSlot(1, slot1Combo.currentIndex)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Client 2"
                        color: Theme.textMuted
                        font.pixelSize: 11
                        Layout.preferredWidth: 64
                    }

                    ComboBox {
                        id: slot2Combo
                        Layout.fillWidth: true
                        model: backend.windowList
                        displayText: count > 0 ? currentText : "Select window..."

                        contentItem: Text {
                            text: slot2Combo.displayText
                            color: Theme.textPrimary
                            font.pixelSize: 11
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: 10
                            rightPadding: 30
                            elide: Text.ElideRight
                        }

                        background: Rectangle {
                            color: Theme.inputBackground
                            radius: Theme.radiusSmall
                            border.width: 1
                            border.color: slot2Combo.hovered ? Theme.borderLight : Theme.border
                        }
                    }

                    ActionButton {
                        text: backend.slot2Attached ? "Attached" : "Attach"
                        baseColor: backend.slot2Attached ? Theme.success : Theme.accent
                        hoverColor: backend.slot2Attached ? Theme.successHover : Theme.accentHover
                        Layout.preferredWidth: 90
                        implicitHeight: 34
                        enabled: slot2Combo.count > 0
                        onClicked: backend.attachWindowToSlot(2, slot2Combo.currentIndex)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    StatusBadge {
                        text: "C1: " + backend.slot1WindowName
                        badgeColor: backend.slot1Attached ? Theme.success : Theme.danger
                    }

                    StatusBadge {
                        text: "C2: " + backend.slot2WindowName
                        badgeColor: backend.slot2Attached ? Theme.success : Theme.danger
                    }

                    Item { Layout.fillWidth: true }

                    ActionButton {
                        text: "Refresh"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitHeight: 34
                        Layout.preferredWidth: 90
                        onClicked: backend.refreshWindows()
                    }
                }

                ToggleOption {
                    text: "Show all windows (advanced)"
                    checked: false
                    onToggled: backend.setShowAllWindows(checked)
                }
            }

            Card {
                title: "Vision & CAPTCHA"
                Layout.fillWidth: true

                Label {
                    text: "Shared vision confidence applies to Client 1 and Client 2."
                    color: Theme.textMuted
                    font.pixelSize: 10
                }

                SliderOption {
                    text: "Vision Confidence"
                    from: 0.1
                    to: 1.0
                    stepSize: 0.05
                    value: globalTab.globalConfidence
                    onValueEdited: {
                        globalTab.globalConfidence = value
                        globalTab.applyConfidenceToAll(value)
                    }
                }

                ToggleOption {
                    text: "Enable Global CAPTCHA Solver"
                    checked: backend.captchaEnabled
                    onToggled: backend.captchaEnabled = checked
                }

                ComboOption {
                    text: "Captcha Provider"
                    modelData: ["Google Gemini"]
                    currentIndex: 0
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "API Key"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        Layout.preferredWidth: 64
                    }

                    TextField {
                        id: apiKeyField
                        Layout.fillWidth: true
                        text: backend.captchaApiKey
                        echoMode: TextInput.Password
                        placeholderText: "AIza..."
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        onEditingFinished: backend.captchaApiKey = text

                        background: Rectangle {
                            color: Theme.inputBackground
                            radius: Theme.radiusSmall
                            border.width: 1
                            border.color: Theme.border
                        }
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Model"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        Layout.preferredWidth: 64
                    }

                    ComboBox {
                        id: modelCombo
                        Layout.fillWidth: true
                        model: captchaModelNames
                        enabled: captchaModelNames.length > 0
                        font.pixelSize: 11

                        contentItem: Text {
                            text: modelCombo.displayText
                            color: Theme.textPrimary
                            font.pixelSize: 11
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: 10
                            rightPadding: 30
                            elide: Text.ElideRight
                        }

                        background: Rectangle {
                            color: Theme.inputBackground
                            radius: Theme.radiusSmall
                            border.width: 1
                            border.color: modelCombo.hovered ? Theme.borderLight : Theme.border
                        }

                        onActivated: {
                            if (currentIndex >= 0 && currentIndex < captchaModelNames.length)
                                backend.captchaSelectedModel = captchaModelNames[currentIndex]
                        }
                    }

                    ActionButton {
                        text: "Fetch"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitHeight: 34
                        Layout.preferredWidth: 84
                        onClicked: {
                            const fetched = backend.fetch_gemini_models(apiKeyField.text)
                            if (fetched && fetched.length > 0) {
                                captchaModelNames = fetched
                                syncCaptchaSelection()
                            }
                        }
                    }
                }
            }

            Card {
                title: "System"
                Layout.fillWidth: true

                SpinOption {
                    text: "Mouse Device ID"
                    from: 0
                    to: 100
                    value: backend.mouseId
                    onValueChanged: backend.mouseId = value
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    ActionButton {
                        text: "Calibrate Mouse"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitHeight: 36
                        Layout.preferredWidth: 150
                        onClicked: backend.calibrateMouse()
                    }

                    Item { Layout.fillWidth: true }

                    ActionButton {
                        text: "Save Global Settings"
                        baseColor: Theme.accent
                        hoverColor: Theme.accentHover
                        implicitHeight: 36
                        Layout.preferredWidth: 170
                        onClicked: backend.saveConfig()
                    }
                }
            }

            Item { height: 10 }
        }
    }
}
