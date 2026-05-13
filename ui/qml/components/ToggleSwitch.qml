import QtQuick
import QtQuick.Controls

Control {
    id: root
    property bool checked: false
    property color activeColor: Theme.accent
    property color inactiveColor: Theme.switchOff
    property color thumbColor: Theme.switchThumb
    signal toggled(bool checked)

    implicitWidth: 48
    implicitHeight: 28
    focusPolicy: Qt.StrongFocus

    function toggleFromUser() {
        if (!root.enabled)
            return
        root.checked = !root.checked
        root.toggled(root.checked)
    }

    Keys.onSpacePressed: root.toggleFromUser()
    Keys.onReturnPressed: root.toggleFromUser()
    Keys.onEnterPressed: root.toggleFromUser()

    contentItem: Item {
        Rectangle {
            anchors.fill: parent
            anchors.margins: 1
            radius: height / 2
            color: root.checked ? root.activeColor : root.inactiveColor
            opacity: root.enabled ? 1.0 : 0.5

            Behavior on color {
                ColorAnimation { duration: 140 }
            }
        }

        Rectangle {
            id: knob
            width: parent.height - 8
            height: parent.height - 8
            radius: width / 2
            y: 4
            x: root.checked ? parent.width - width - 4 : 4
            color: root.thumbColor
            border.width: 1
            border.color: "#24000000"

            Behavior on x {
                NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        enabled: root.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggleFromUser()
    }
}