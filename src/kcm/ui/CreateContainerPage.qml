/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs

import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import org.kde.kcmutils as KCMUtils

KCMUtils.SimpleKCM {
    id: createPage

    title: i18n("Create Container")

    KCMUtils.ConfigModule.buttons: KCMUtils.ConfigModule.NoAdditionalButton

    // Internal state: tracks user-set option values (only non-defaults are sent)
    property var optionValues: ({})

    // Whether create is in progress
    property bool creating: false

    Connections {
        target: kcm
        function onContainerCreated() {
            createPage.creating = false;
            kcm.pop();
        }
        function onOperationFailed(message) {
            createPage.creating = false;
        }
    }

    // Helper: check if a dependency map is satisfied by current option values
    function dependenciesMet(deps) {
        if (!deps || Object.keys(deps).length === 0) {
            return true;
        }
        for (let key in deps) {
            let requiredValue = deps[key];
            // Check user-set value first, then fall back to checking defaults in schema
            let currentValue = getOptionValue(key);
            if (currentValue !== requiredValue) {
                return false;
            }
        }
        return true;
    }

    // Get the effective value for an option key (user-set or schema default)
    function getOptionValue(key) {
        if (key in optionValues) {
            return optionValues[key];
        }
        // Look up default from schema
        return getDefaultValue(key);
    }

    // Look up the schema default for a key
    function getDefaultValue(key) {
        let schemaModel = kcm.schemaModel;
        for (let s = 0; s < schemaModel.rowCount(); s++) {
            let sectionIndex = schemaModel.index(s, 0);
            let optModel = sectionIndex.data(Qt.UserRole + 3); // OptionsModelRole
            if (!optModel) continue;
            for (let o = 0; o < optModel.rowCount(); o++) {
                let optIndex = optModel.index(o, 0);
                let optKey = optModel.data(optIndex, Qt.UserRole + 1); // KeyRole
                if (optKey === key) {
                    return optModel.data(optIndex, Qt.UserRole + 5); // DefaultValueRole
                }
            }
        }
        return undefined;
    }

    // Set an option value (only stores if different from default)
    function setOptionValue(key, value) {
        let defVal = getDefaultValue(key);
        let newValues = Object.assign({}, optionValues);
        if (value === defVal) {
            delete newValues[key];
        } else {
            newValues[key] = value;
        }
        optionValues = newValues;
    }

    ColumnLayout {
        spacing: 0

        // Error banner
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: kcm.statusMessage.length > 0 && createPage.creating === false
            text: kcm.statusMessage
            type: Kirigami.MessageType.Error
        }

        // Basic info section
        FormCard.FormHeader {
            title: i18n("Container")
        }

        FormCard.FormCard {
            FormCard.FormTextFieldDelegate {
                id: nameField
                label: i18n("Name")
                placeholderText: i18n("e.g. dev-ubuntu")
                enabled: !createPage.creating
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextFieldDelegate {
                id: imageField
                label: i18n("Image")
                text: kcm.defaultImage
                placeholderText: i18n("e.g. images:ubuntu/24.04")
                enabled: !createPage.creating
            }
        }

        // Dynamic schema sections
        Repeater {
            model: kcm.schemaModel

            delegate: ColumnLayout {
                id: sectionDelegate
                spacing: 0
                Layout.fillWidth: true

                required property var model
                required property int index

                FormCard.FormHeader {
                    title: sectionDelegate.model.sectionTitle
                }

                FormCard.FormCard {
                    Repeater {
                        model: sectionDelegate.model.optionsModel

                        delegate: Loader {
                            width: parent ? parent.width : 0

                            required property var model
                            required property int index

                            // Forward model properties for access inside Components
                            property string optKey: model.key
                            property string optType: model.type
                            property string optTitle: model.title
                            property string optDescription: model.description
                            property var optDependencies: model.dependencies
                            property string optItemFormat: model.itemFormat

                            sourceComponent: {
                                switch (optType) {
                                case "boolean":
                                    return booleanOptionComponent;
                                case "string":
                                    return stringOptionComponent;
                                case "array":
                                    return arrayOptionComponent;
                                default:
                                    return null;
                                }
                            }
                        }
                    }
                }
            }
        }

        // Create button
        FormCard.FormCard {
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormButtonDelegate {
                text: createPage.creating ? i18n("Creating…") : i18n("Create Container")
                icon.name: "list-add"
                enabled: nameField.text.length > 0 && !createPage.creating
                onClicked: {
                    createPage.creating = true;
                    kcm.createContainer(nameField.text, imageField.text, createPage.optionValues);
                }
            }
        }
    }

    // =========================================================================
    // Option type components — loaded by the Loader delegate above.
    // The parent of the loaded item is the Loader, so model properties
    // are accessed via parent.optKey, parent.optTitle, etc.
    // =========================================================================

    Component {
        id: booleanOptionComponent

        FormCard.FormSwitchDelegate {
            text: parent ? parent.optTitle : ""
            description: parent ? parent.optDescription : ""
            checked: parent ? createPage.getOptionValue(parent.optKey) === true : false
            enabled: !createPage.creating
                     && (parent ? createPage.dependenciesMet(parent.optDependencies) : true)
            onToggled: {
                if (parent) {
                    createPage.setOptionValue(parent.optKey, checked);
                }
            }
        }
    }

    Component {
        id: stringOptionComponent

        FormCard.FormTextFieldDelegate {
            label: parent ? parent.optTitle : ""
            text: parent ? (createPage.getOptionValue(parent.optKey) ?? "") : ""
            placeholderText: parent ? parent.optDescription : ""
            enabled: !createPage.creating
                     && (parent ? createPage.dependenciesMet(parent.optDependencies) : true)
            onTextEdited: {
                if (parent) {
                    createPage.setOptionValue(parent.optKey, text);
                }
            }
        }
    }

    Component {
        id: arrayOptionComponent

        FormCard.FormButtonDelegate {
            text: parent ? parent.optTitle : ""
            description: parent ? parent.optDescription : ""
            icon.name: "list-add"
            enabled: !createPage.creating
                     && (parent ? createPage.dependenciesMet(parent.optDependencies) : true)
            onClicked: {
                if (parent && parent.optItemFormat === "directory-path") {
                    folderDialog.optionKey = parent.optKey;
                    folderDialog.open();
                }
            }
        }
    }

    // Folder dialog for directory-path array items
    FolderDialog {
        id: folderDialog
        title: i18n("Select Directory")
        property string optionKey: ""
        onAccepted: {
            if (optionKey.length > 0) {
                let currentVals = createPage.getOptionValue(optionKey) ?? [];
                let path = selectedFolder.toString().replace("file://", "");
                currentVals = currentVals.concat([path]);
                createPage.setOptionValue(optionKey, currentVals);
            }
        }
    }
}
