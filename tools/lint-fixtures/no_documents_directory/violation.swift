let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask) // expect: no_documents_directory
let documentsURL = URL.documentsDirectory // expect: no_documents_directory
let shortDocuments: URL = .documentsDirectory // expect: no_documents_directory
let spacedDocuments = URL . documentsDirectory // expect: no_documents_directory
