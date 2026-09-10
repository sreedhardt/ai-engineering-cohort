export type Source = {
  source_document: string;
  section_title: string;
  collection: string;
};

export type ChatResponse = {
  answer: string;
  sources: Source[];
  retrieval_type: "hybrid_rag" | "sql_rag" | "rbac_denied";
  role: string;
  access_denied: boolean;
};

export type Session = {
  token: string;
  role: string;
  displayName: string;
  department: string;
  collections: string[];
};

export type Message = {
  id: string;
  author: "user" | "bot";
  text: string;
  sources?: Source[];
  retrievalType?: ChatResponse["retrieval_type"];
  accessDenied?: boolean;
  pending?: boolean;
};
