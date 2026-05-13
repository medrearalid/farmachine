import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property alias text: label.text
    property alias key: keyField.text
    property int cooldown: 60
    property bool skillEnabled: false
    property string iconSource: ""
    signal keyEdited(string value)
    signal cooldownEdited(int value)
    signal enabledEdited(bool value)
    Layout.fillWidth: true
    spacing: Theme.spacingMedium

    function clampCooldown(v) {
        var raw = Math.round(v)
        if (raw < 1)
            return 1
        if (raw > 600)
            return 600
        return raw
    }

    function decrementCooldown() {
        var nextValue = clampCooldown(cooldown - 1)
        if (nextValue === cooldown)
            return
        cooldown = nextValue
        cooldownEdited(nextValue)
    }

    function incrementCooldown() {
        var nextValue = clampCooldown(cooldown + 1)
        if (nextValue === cooldown)
            return
        cooldown = nextValue
        cooldownEdited(nextValue)
    }

    onCooldownChanged: {
        if (cooldown !== clampCooldown(cooldown))
            cooldown = clampCooldown(cooldown)
    }

    ToggleSwitch {
        id: toggle
        checked: root.skillEnabled
        onToggled: function(state) {
            if (root.skillEnabled === state)
                return
            root.skillEnabled = state
            root.enabledEdited(state)
        }
    }

    Rectangle {
        visible: iconSource.length > 0
        Layout.preferredWidth: 22
        Layout.preferredHeight: 22
        radius: Theme.radiusSmall
        color: Theme.inputBackground
        border.color: Theme.border
        border.width: 1

        Image {
            anchors.fill: parent
            anchors.margins: 1
            source: iconSource
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false
        }
    }

    Label {
        id: label
        color: root.skillEnabled ? Theme.textPrimary : Theme.textMuted
        font { pixelSize: 11; family: "Segoe UI" }
        Layout.fillWidth: true
        elide: Text.ElideRight
    }

    Label {
        text: "Key:"
        color: Theme.textMuted
        font { pixelSize: 9; family: "Segoe UI" }
    }

    Rectangle {
        Layout.preferredWidth: 50
        implicitHeight: 28
        radius: Theme.radiusSmall
        color: Theme.inputBackground
        border.color: Theme.border
        border.width: 1

        TextInput {
            id: keyField
            anchors.fill: parent
            anchors.margins: 4
            horizontalAlignment: TextInput.AlignHCenter
            verticalAlignment: TextInput.AlignVCenter
            font { pixelSize: 11; family: "Segoe UI"; bold: true }
            color: Theme.textPrimary
            selectionColor: Theme.accent
            selectedTextColor: "#ffffff"
            clip: true
            onEditingFinished: {
                var normalized = text.trim().toLowerCase()
                if (text !== normalized)
                    text = normalized
                root.keyEdited(normalized)
            }
        }
    }

    Label {
        text: "CD:"
        color: Theme.textMuted
        font { pixelSize: 9; family: "Segoe UI" }
    }

    Rectangle {
        Layout.preferredWidth: 104
        Layout.minimumWidth: 98
        implicitHeight: 32
        radius: Theme.radiusMedium
        color: Theme.inputBackground
        border.width: 1
        border.color: Theme.borderLight

        RowLayout {
            anchors.fill: parent
            anchors.margins: 2
            spacing: 2

            Rectangle {
                Layout.preferredWidth: 30
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: decArea.pressed ? Theme.borderLight : Theme.bgTertiary

                Label {
                    anchors.centerIn: parent
                    text: "−"
                    color: Theme.textPrimary
                    font { pixelSize: 15; bold: true; family: "Segoe UI" }
                }

                MouseArea {
                    id: decArea
                    anchors.fill: parent
                    onClicked: root.decrementCooldown()
                }
            }

            Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: root.cooldown.toString() + "s"
                color: Theme.textPrimary
                font { pixelSize: 10; family: "Segoe UI Semibold" }
            }

            Rectangle {
                Layout.preferredWidth: 30
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: incArea.pressed ? Theme.borderLight : Theme.bgTertiary

                Label {
                    anchors.centerIn: parent
                    text: "+"
                    color: Theme.textPrimary
                    font { pixelSize: 15; bold: true; family: "Segoe UI" }
                }

                MouseArea {
                    id: incArea
                    anchors.fill: parent
                    onClicked: root.incrementCooldown()
                }
            }
        }
    }
}
